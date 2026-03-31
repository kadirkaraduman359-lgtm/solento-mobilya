from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, abort
from flask_login import login_required, current_user
from functools import wraps
from models import (db, Urun, UrunPaketi, SiparisTalebi, SiparisTalebiKalemi,
                    SshBildirimi, StokHareketi, Sevk, SevkKalemi, SatisHareketi,
                    KatalogUrun, KatalogMagazaIzin)
from datetime import datetime

magaza_bp = Blueprint("magaza", __name__)


def magaza_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.is_admin:
            return redirect(url_for("admin.dashboard"))
        return f(*args, **kwargs)
    return decorated


def magaza_stok_ozet(magaza_id):
    """Mağaza bazlı: gelen takım - satılan = eldeki."""
    urunler = Urun.query.all()
    ozet = []
    for u in urunler:
        gelen = db.session.query(db.func.coalesce(db.func.sum(SevkKalemi.miktar), 0)).join(
            Sevk, SevkKalemi.sevk_id == Sevk.id
        ).filter(
            Sevk.magaza_id == magaza_id,
            SevkKalemi.urun_id == u.id
        ).scalar() or 0

        satilan = db.session.query(db.func.coalesce(db.func.sum(SatisHareketi.miktar), 0)).filter(
            SatisHareketi.magaza_id == magaza_id,
            SatisHareketi.urun_id == u.id
        ).scalar() or 0

        if gelen > 0 or satilan > 0:
            ozet.append({
                "urun": u,
                "gelen": gelen,
                "satilan": satilan,
                "eldeki": gelen - satilan
            })
    return ozet


@magaza_bp.route("/")
@magaza_required
def dashboard():
    stok = magaza_stok_ozet(current_user.magaza_id)
    acik_talepler = SiparisTalebi.query.filter_by(
        magaza_id=current_user.magaza_id
    ).filter(SiparisTalebi.durum != "iptal").order_by(SiparisTalebi.id.desc()).limit(5).all()
    acik_ssh = SshBildirimi.query.filter_by(
        magaza_id=current_user.magaza_id
    ).filter(SshBildirimi.durum != "teslim_edildi").order_by(SshBildirimi.id.desc()).limit(5).all()
    return render_template("magaza/dashboard.html", stok=stok,
                           acik_talepler=acik_talepler, acik_ssh=acik_ssh)


@magaza_bp.route("/stok")
@magaza_required
def stok_gorunum():
    from models import StokHareketi, SiparisTalebiKalemi
    from sqlalchemy import func
    GIRIS_TURLERI = ["uretim_giris", "duzeltme_giris", "iade"]
    CIKIS_TURLERI = ["sevk_cikis", "duzeltme_cikis", "fire"]

    # Tüm aktif taleplerdeki rezerveleri hesapla (bu mağaza DAHİL tüm mağazalar)
    rezerve_map = {}
    aktif_kalemler = (
        db.session.query(SiparisTalebiKalemi)
        .join(SiparisTalebi, SiparisTalebiKalemi.talep_id == SiparisTalebi.id)
        .filter(SiparisTalebi.durum.in_(["beklemede", "onaylandi"]))
        .all()
    )
    for k in aktif_kalemler:
        rezerve_map[k.urun_id] = rezerve_map.get(k.urun_id, 0) + k.miktar

    # Ana depo stoku + kullanılabilir hesapla
    urunler = Urun.query.order_by(Urun.ad).all()
    depo_stok = []
    stok_yok = []  # Stokta olmayan ama talep edilebilir ürünler
    for u in urunler:
        giris = db.session.query(func.coalesce(func.sum(StokHareketi.miktar), 0))\
            .filter(StokHareketi.urun_id == u.id,
                    StokHareketi.hareket_turu.in_(GIRIS_TURLERI)).scalar() or 0
        cikis = db.session.query(func.coalesce(func.sum(func.abs(StokHareketi.miktar)), 0))\
            .filter(StokHareketi.urun_id == u.id,
                    StokHareketi.hareket_turu.in_(CIKIS_TURLERI)).scalar() or 0
        bakiye = int(giris) - int(cikis)
        rezerve = rezerve_map.get(u.id, 0)
        kullanilabilir = max(0, bakiye - rezerve)
        if bakiye > 0:
            depo_stok.append({
                "urun": u,
                "bakiye": bakiye,
                "rezerve": rezerve,
                "kullanilabilir": kullanilabilir
            })
        elif bakiye <= 0 and (giris > 0):
            # Ürün daha önce stokta vardı ama bitti
            stok_yok.append({"urun": u})

    kendi_stok = magaza_stok_ozet(current_user.magaza_id)
    bekleyen_talepler = SiparisTalebi.query.filter_by(
        magaza_id=current_user.magaza_id
    ).filter(SiparisTalebi.durum.in_(["beklemede", "onaylandi"])).order_by(SiparisTalebi.id.desc()).all()
    return render_template("magaza/stok.html",
                           depo_stok=depo_stok,
                           stok_yok=stok_yok,
                           kendi_stok=kendi_stok,
                           bekleyen_talepler=bekleyen_talepler)


@magaza_bp.route("/stok/siparis", methods=["POST"])
@magaza_required
def hizli_siparis():
    notlar = request.form.get("notlar", "")
    kalemler = []
    # Format 1: miktar_URUNID (çoklu ürün formu)
    for key, val in request.form.items():
        if key.startswith("miktar_") and val:
            try:
                if int(val) > 0:
                    urun_id = int(key.replace("miktar_", ""))
                    kalemler.append((urun_id, int(val)))
            except (ValueError, TypeError):
                pass
    # Format 2: urun_id + miktar (tekli modal formu)
    if not kalemler:
        urun_id = request.form.get("urun_id", type=int)
        miktar = request.form.get("miktar", type=int)
        if urun_id and miktar and miktar > 0:
            kalemler.append((urun_id, miktar))
    if not kalemler:
        flash("En az 1 ürün seçmelisiniz.", "warning")
        return redirect(url_for("magaza.stok_gorunum"))
    talep = SiparisTalebi(
        magaza_id=current_user.magaza_id,
        kullanici_id=current_user.id,
        tarih=datetime.now().strftime("%Y-%m-%d %H:%M"),
        notlar=notlar,
        durum="beklemede"
    )
    db.session.add(talep)
    db.session.flush()
    for urun_id, miktar in kalemler:
        db.session.add(SiparisTalebiKalemi(
            talep_id=talep.id, urun_id=urun_id, miktar=miktar))
    db.session.commit()
    flash(f"{len(kalemler)} ürün için sipariş talebi gönderildi. Yönetici onayı bekleniyor.", "success")
    return redirect(url_for("magaza.stok_gorunum"))


@magaza_bp.route("/stok/stok-talep", methods=["POST"])
@magaza_required
def stok_talep():
    """Stokta olmayan ürün için talep gönder (ön sipariş)."""
    urun_id = request.form.get("urun_id", type=int)
    miktar = request.form.get("miktar", type=int) or 1
    notlar = request.form.get("notlar", "").strip()
    urun = Urun.query.get_or_404(urun_id)
    talep = SiparisTalebi(
        magaza_id=current_user.magaza_id,
        kullanici_id=current_user.id,
        tarih=datetime.now().strftime("%Y-%m-%d %H:%M"),
        notlar=f"[STOK YOK - ÖN SİPARİŞ] {notlar}",
        durum="beklemede"
    )
    db.session.add(talep)
    db.session.flush()
    db.session.add(SiparisTalebiKalemi(talep_id=talep.id, urun_id=urun_id, miktar=miktar))
    db.session.commit()
    flash(f"'{urun.ad}' için ön sipariş talebi gönderildi.", "success")
    return redirect(url_for("magaza.stok_gorunum"))


@magaza_bp.route("/stok/iptal/<int:talep_id>", methods=["POST"])
@magaza_required
def siparis_iptal(talep_id):
    talep = SiparisTalebi.query.get_or_404(talep_id)
    if talep.magaza_id != current_user.magaza_id:
        flash("Bu işlem için yetkiniz yok.", "danger")
        return redirect(url_for("magaza.stok_gorunum"))
    if talep.durum not in ("beklemede",):
        flash("Sadece 'Beklemede' durumundaki talepler iptal edilebilir.", "warning")
        return redirect(url_for("magaza.stok_gorunum"))
    sebep = request.form.get("iptal_sebebi", "").strip()
    if not sebep:
        flash("Lütfen iptal sebebi yazın.", "warning")
        return redirect(url_for("magaza.stok_gorunum"))
    talep.durum = "iptal"
    talep.iptal_sebebi = sebep
    db.session.commit()
    flash("Sipariş talebi iptal edildi.", "success")
    return redirect(url_for("magaza.stok_gorunum"))


@magaza_bp.route("/satis", methods=["GET", "POST"])
@magaza_required
def satis_gir():
    stok = magaza_stok_ozet(current_user.magaza_id)
    urunler_eldeki = [s for s in stok if s["eldeki"] > 0]
    if request.method == "POST":
        tarih = request.form.get("tarih", datetime.now().strftime("%Y-%m-%d"))
        urun_ids = request.form.getlist("urun_id[]")
        miktarlar = request.form.getlist("miktar[]")
        notlar_list = request.form.getlist("notlar[]")
        kaydedilen = 0
        for uid, mkt, not_ in zip(urun_ids, miktarlar, notlar_list):
            uid = int(uid)
            mkt = float(mkt or 0)
            if mkt <= 0:
                continue
            eldeki = next((s["eldeki"] for s in stok if s["urun"].id == uid), 0)
            if mkt > eldeki:
                urun = Urun.query.get(uid)
                flash(f"'{urun.ad}' için stok yetersiz. Eldeki: {eldeki:.0f}", "danger")
                return render_template("magaza/satis.html", stok=stok, urunler_eldeki=urunler_eldeki)
            db.session.add(SatisHareketi(
                tarih=tarih,
                magaza_id=current_user.magaza_id,
                kullanici_id=current_user.id,
                urun_id=uid,
                miktar=mkt,
                notlar=not_.strip()
            ))
            kaydedilen += 1
        db.session.commit()
        flash(f"{kaydedilen} ürün satışı kaydedildi.", "success")
        return redirect(url_for("magaza.satis_gecmis"))
    return render_template("magaza/satis.html", stok=stok, urunler_eldeki=urunler_eldeki)


@magaza_bp.route("/satis/gecmis")
@magaza_required
def satis_gecmis():
    satirlar = SatisHareketi.query.filter_by(
        magaza_id=current_user.magaza_id
    ).order_by(SatisHareketi.id.desc()).all()
    return render_template("magaza/satis_gecmis.html", satirlar=satirlar)


@magaza_bp.route("/talep", methods=["GET", "POST"])
@magaza_required
def talep_olustur():
    urunler = Urun.query.order_by(Urun.ad).all()
    if request.method == "POST":
        notlar = request.form.get("notlar", "").strip()
        urun_ids = request.form.getlist("urun_id[]")
        miktarlar = request.form.getlist("miktar[]")
        kalemler = [(int(uid), float(mkt or 0))
                    for uid, mkt in zip(urun_ids, miktarlar) if uid and float(mkt or 0) > 0]
        if not kalemler:
            flash("En az bir urun eklemelisiniz.", "warning")
            return render_template("magaza/talep.html", urunler=urunler)
        t = SiparisTalebi(
            magaza_id=current_user.magaza_id,
            kullanici_id=current_user.id,
            notlar=notlar,
            tarih=datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        db.session.add(t)
        db.session.flush()
        for uid, mkt in kalemler:
            db.session.add(SiparisTalebiKalemi(talep_id=t.id, urun_id=uid, miktar=mkt))
        db.session.commit()
        flash("Siparis talebiniz gonderildi.", "success")
        return redirect(url_for("magaza.taleplerim"))
    return render_template("magaza/talep.html", urunler=urunler)


@magaza_bp.route("/taleplerim")
@magaza_required
def taleplerim():
    talepler = SiparisTalebi.query.filter_by(
        magaza_id=current_user.magaza_id
    ).order_by(SiparisTalebi.id.desc()).all()
    return render_template("magaza/taleplerim.html", talepler=talepler)


@magaza_bp.route("/ssh", methods=["GET", "POST"])
@magaza_required
def ssh_bildir():
    urunler = Urun.query.order_by(Urun.ad).all()
    if request.method == "POST":
        urun_id = request.form.get("urun_id", type=int)
        paket_id = request.form.get("paket_id", type=int)
        hasar = request.form.get("hasar_aciklamasi", "").strip()
        miktar = request.form.get("talep_miktar", type=float, default=1)
        if not urun_id or not hasar:
            flash("Urun ve hasar aciklamasi zorunlu.", "warning")
            return render_template("magaza/ssh.html", urunler=urunler)
        b = SshBildirimi(
            magaza_id=current_user.magaza_id,
            kullanici_id=current_user.id,
            urun_id=urun_id,
            paket_id=paket_id if paket_id else None,
            hasar_aciklamasi=hasar,
            talep_miktar=miktar,
            tarih=datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        db.session.add(b)
        db.session.commit()
        flash("SSH bildirimi gonderildi.", "success")
        return redirect(url_for("magaza.ssh_gecmis"))
    return render_template("magaza/ssh.html", urunler=urunler)


@magaza_bp.route("/ssh/gecmis")
@magaza_required
def ssh_gecmis():
    bildirimleri = SshBildirimi.query.filter_by(
        magaza_id=current_user.magaza_id
    ).order_by(SshBildirimi.id.desc()).all()
    return render_template("magaza/ssh_gecmis.html", bildirimleri=bildirimleri)


@magaza_bp.route("/api/urun-paketleri/<int:urun_id>")
@magaza_required
def urun_paketleri(urun_id):
    from flask import jsonify
    paketler = UrunPaketi.query.filter_by(urun_id=urun_id).order_by(UrunPaketi.paket_no).all()
    return jsonify([{"id": p.id, "ad": f"Paket {p.paket_no} - {p.paket_adi}"} for p in paketler])


@magaza_bp.route("/sevklerim")
@magaza_required
def sevklerim():
    sevkler = Sevk.query.filter_by(magaza_id=current_user.magaza_id).order_by(Sevk.id.desc()).all()
    return render_template("magaza/sevklerim.html", sevkler=sevkler)


@magaza_bp.route("/sevklerim/<int:sevk_id>/teslim-al", methods=["POST"])
@magaza_required
def teslim_al(sevk_id):
    sevk = Sevk.query.get_or_404(sevk_id)
    if sevk.magaza_id != current_user.magaza_id:
        flash("Bu işlem için yetkiniz yok.", "danger")
        return redirect(url_for("magaza.sevklerim"))
    if sevk.teslim_durumu == "teslim_alindi":
        flash("Bu sevk zaten teslim alınmış.", "warning")
        return redirect(url_for("magaza.sevklerim"))
    sevk.teslim_durumu = "teslim_alindi"
    sevk.teslim_tarihi = datetime.now().strftime("%Y-%m-%d %H:%M")
    db.session.commit()
    flash("Teslim alındı olarak işaretlendi.", "success")
    return redirect(url_for("magaza.sevklerim"))


# ─── KATALOG ─────────────────────────────────────────────────────────────────

@magaza_bp.route("/katalog")
@magaza_required
def katalog():
    if not current_user.yetkisi_var_mi("katalog"):
        abort(403)
    magaza_id = current_user.magaza_id
    fiyat_gorunsun = current_user.yetkisi_var_mi("katalog_fiyat")
    # Görünürlük filtresi
    tum = KatalogUrun.query.filter_by(aktif=True)
    gorunen = []
    for ku in tum:
        if ku.gorunurluk == "herkes":
            gorunen.append((ku, fiyat_gorunsun and ku.fiyat_onaylandi))
        elif ku.gorunurluk == "secili":
            izin = KatalogMagazaIzin.query.filter_by(
                katalog_urun_id=ku.id, magaza_id=magaza_id).first()
            if izin:
                gorunen.append((ku, fiyat_gorunsun and izin.fiyat_gorunsun and ku.fiyat_onaylandi))
        # gorunurluk == "gizli" → atla
    return render_template("magaza/katalog.html", urunler=gorunen)


@magaza_bp.route("/katalog/<int:id>")
@magaza_required
def katalog_detay(id):
    if not current_user.yetkisi_var_mi("katalog"):
        abort(403)
    ku = KatalogUrun.query.get_or_404(id)
    magaza_id = current_user.magaza_id
    fiyat_gorunsun = False
    if ku.gorunurluk == "gizli":
        abort(403)
    elif ku.gorunurluk == "secili":
        izin = KatalogMagazaIzin.query.filter_by(
            katalog_urun_id=ku.id, magaza_id=magaza_id).first()
        if not izin:
            abort(403)
        fiyat_gorunsun = current_user.yetkisi_var_mi("katalog_fiyat") and izin.fiyat_gorunsun and ku.fiyat_onaylandi
    else:
        fiyat_gorunsun = current_user.yetkisi_var_mi("katalog_fiyat") and ku.fiyat_onaylandi
    return render_template("magaza/katalog_detay.html", ku=ku, fiyat_gorunsun=fiyat_gorunsun)


@magaza_bp.route("/katalog/siparis", methods=["POST"])
@magaza_required
def katalog_siparis():
    from models import SiparisTalebi, SiparisTalebiKalemi, Urun, KatalogUrun
    katalog_urun_id = request.form.get("katalog_urun_id", type=int)
    miktar = request.form.get("miktar", type=int) or 1
    notlar = request.form.get("notlar", "").strip()
    ku = KatalogUrun.query.get_or_404(katalog_urun_id)
    # Katalog ürün adıyla stok ürünü bul
    urun = Urun.query.filter(db.func.upper(Urun.ad) == ku.ad.upper()).first()
    if not urun:
        # Stok ürünü yoksa yalnızca talep oluştur (ürün adı nota yaz)
        talep = SiparisTalebi(
            magaza_id=current_user.magaza_id,
            kullanici_id=current_user.id,
            durum="beklemede",
            notlar=f"[Katalog: {ku.ad}] {notlar}"
        )
        db.session.add(talep)
        db.session.flush()
        flash(f"'{ku.ad}' için sipariş talebi gönderildi.", "success")
    else:
        talep = SiparisTalebi(
            magaza_id=current_user.magaza_id,
            kullanici_id=current_user.id,
            durum="beklemede",
            notlar=notlar
        )
        db.session.add(talep)
        db.session.flush()
        db.session.add(SiparisTalebiKalemi(talep_id=talep.id, urun_id=urun.id, miktar=miktar))
        flash(f"'{ku.ad}' — {miktar} adet sipariş talebi gönderildi.", "success")
    db.session.commit()
    return redirect(url_for("magaza.katalog"))


@magaza_bp.route("/katalog/fiyat-teklifi", methods=["POST"])
@magaza_required
def fiyat_teklifi_gonder():
    from models import FiyatTeklifi
    katalog_urun_id = request.form.get("katalog_urun_id", type=int)
    miktar = request.form.get("miktar", type=int) or 1
    not_ = request.form.get("not_", "").strip()
    ft = FiyatTeklifi(
        katalog_urun_id=katalog_urun_id,
        magaza_id=current_user.magaza_id,
        kullanici_id=current_user.id,
        miktar=miktar,
        not_=not_,
        durum="beklemede"
    )
    db.session.add(ft)
    db.session.commit()
    flash("Fiyat teklifi talebiniz yöneticiye iletildi.", "success")
    return redirect(url_for("magaza.katalog"))
