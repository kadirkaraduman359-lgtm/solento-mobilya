from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, abort
from flask_login import login_required, current_user
from functools import wraps
from models import (db, Urun, UrunPaketi, SiparisTalebi, SiparisTalebiKalemi,
                    SshBildirimi, StokHareketi, Sevk, SevkKalemi, SatisHareketi,
                    KatalogUrun, KatalogMagazaIzin, FiyatTeklifi)
from datetime import datetime

magaza_bp = Blueprint("magaza", __name__)

GIRIS_TURLERI = ["uretim_giris", "duzeltme_giris", "iade"]
CIKIS_TURLERI = ["sevk_cikis", "duzeltme_cikis", "fire"]


def magaza_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.is_admin:
            return redirect(url_for("admin.dashboard"))
        if current_user.onay_durumu != "onaylandi":
            flash("Hesabınız henüz onaylanmadı.", "warning")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated


def yetki_gerekli(alan):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.yetkisi_var_mi(alan):
                flash("Bu bölüme erişim yetkiniz yok.", "danger")
                return redirect(url_for("magaza.dashboard"))
            return f(*args, **kwargs)
        return decorated
    return decorator


def magaza_stok_ozet(magaza_id):
    urunler = Urun.query.all()
    ozet = []
    for u in urunler:
        gelen = db.session.query(db.func.coalesce(db.func.sum(SevkKalemi.miktar), 0)).join(
            Sevk, SevkKalemi.sevk_id == Sevk.id
        ).filter(Sevk.magaza_id == magaza_id, SevkKalemi.urun_id == u.id).scalar() or 0

        satilan = db.session.query(db.func.coalesce(db.func.sum(SatisHareketi.miktar), 0)).filter(
            SatisHareketi.magaza_id == magaza_id, SatisHareketi.urun_id == u.id
        ).scalar() or 0

        if gelen > 0 or satilan > 0:
            ozet.append({
                "urun": u,
                "gelen": float(gelen),
                "satilan": float(satilan),
                "eldeki": float(gelen) - float(satilan)
            })
    return ozet


# ---------------------------------------------------------------------------
# 1. Dashboard
# ---------------------------------------------------------------------------
@magaza_bp.route("/")
@magaza_required
def dashboard():
    stok = magaza_stok_ozet(current_user.magaza_id)
    bekleyen_sevk = Sevk.query.filter_by(
        magaza_id=current_user.magaza_id,
        teslim_durumu="sevk_edildi"
    ).count()
    acik_talepler = (
        SiparisTalebi.query
        .filter_by(magaza_id=current_user.magaza_id)
        .filter(SiparisTalebi.durum.in_(["beklemede", "onaylandi"]))
        .order_by(SiparisTalebi.id.desc())
        .limit(5)
        .all()
    )
    acik_ssh = (
        SshBildirimi.query
        .filter_by(magaza_id=current_user.magaza_id)
        .filter(SshBildirimi.durum != "teslim_edildi")
        .order_by(SshBildirimi.id.desc())
        .limit(5)
        .all()
    )
    return render_template(
        "magaza/dashboard.html",
        stok=stok,
        bekleyen_sevk=bekleyen_sevk,
        acik_talepler=acik_talepler,
        acik_ssh=acik_ssh,
    )


# ---------------------------------------------------------------------------
# 2. Stok görünüm
# ---------------------------------------------------------------------------
@magaza_bp.route("/stok")
@magaza_required
@yetki_gerekli("stok")
def stok_gorunum():
    urunler = Urun.query.all()

    # Rezerve hesaplama: tüm magazalardaki beklemede/onaylandi talep kalemleri
    rezerve_map = {}
    aktif_kalemler = (
        SiparisTalebiKalemi.query
        .join(SiparisTalebi, SiparisTalebiKalemi.talep_id == SiparisTalebi.id)
        .filter(SiparisTalebi.durum.in_(["beklemede", "onaylandi"]))
        .all()
    )
    for kalem in aktif_kalemler:
        rezerve_map[kalem.urun_id] = rezerve_map.get(kalem.urun_id, 0) + (kalem.miktar or 0)

    depo_stok = []
    stok_yok = []

    for u in urunler:
        giris = db.session.query(
            db.func.coalesce(db.func.sum(StokHareketi.miktar), 0)
        ).filter(
            StokHareketi.urun_id == u.id,
            StokHareketi.hareket_turu.in_(GIRIS_TURLERI)
        ).scalar() or 0

        cikis = db.session.query(
            db.func.coalesce(db.func.sum(StokHareketi.miktar), 0)
        ).filter(
            StokHareketi.urun_id == u.id,
            StokHareketi.hareket_turu.in_(CIKIS_TURLERI)
        ).scalar() or 0

        bakiye = float(giris) - float(cikis)
        rezerve = float(rezerve_map.get(u.id, 0))
        kullanilabilir = max(0.0, bakiye - rezerve)

        item = {
            "urun": u,
            "bakiye": bakiye,
            "rezerve": rezerve,
            "kullanilabilir": kullanilabilir,
        }

        if bakiye > 0:
            depo_stok.append(item)
        elif float(giris) > 0:
            stok_yok.append(item)

    kendi_stok = magaza_stok_ozet(current_user.magaza_id)
    bekleyen_talepler = (
        SiparisTalebi.query
        .filter_by(magaza_id=current_user.magaza_id)
        .filter(SiparisTalebi.durum.in_(["beklemede", "onaylandi"]))
        .order_by(SiparisTalebi.id.desc())
        .all()
    )

    return render_template(
        "magaza/stok.html",
        depo_stok=depo_stok,
        stok_yok=stok_yok,
        kendi_stok=kendi_stok,
        bekleyen_talepler=bekleyen_talepler,
    )


# ---------------------------------------------------------------------------
# 3. Hızlı sipariş
# ---------------------------------------------------------------------------
@magaza_bp.route("/stok/siparis", methods=["POST"])
@magaza_required
def hizli_siparis():
    notlar = request.form.get("notlar", "").strip()
    kalemler = []
    for key, val in request.form.items():
        if key.startswith("miktar_"):
            try:
                urun_id = int(key.split("_", 1)[1])
                miktar = float(val)
                if miktar > 0:
                    kalemler.append((urun_id, miktar))
            except (ValueError, IndexError):
                continue

    if not kalemler:
        flash("En az bir ürün ve miktar girmelisiniz.", "warning")
        return redirect(url_for("magaza.stok_gorunum"))

    talep = SiparisTalebi(
        magaza_id=current_user.magaza_id,
        olusturan_id=current_user.id,
        durum="beklemede",
        notlar=notlar,
        olusturma_tarihi=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    db.session.add(talep)
    db.session.flush()

    for urun_id, miktar in kalemler:
        kalem = SiparisTalebiKalemi(
            talep_id=talep.id,
            urun_id=urun_id,
            miktar=miktar,
        )
        db.session.add(kalem)

    db.session.commit()
    flash(f"{len(kalemler)} kalem ile sipariş talebiniz oluşturuldu.", "success")
    return redirect(url_for("magaza.stok_gorunum"))


# ---------------------------------------------------------------------------
# 4. Stok talep (ön sipariş)
# ---------------------------------------------------------------------------
@magaza_bp.route("/stok/stok-talep", methods=["POST"])
@magaza_required
def stok_talep():
    urun_id = request.form.get("urun_id", type=int)
    miktar = request.form.get("miktar", type=float)
    notlar = request.form.get("notlar", "").strip()

    talep = SiparisTalebi(
        magaza_id=current_user.magaza_id,
        olusturan_id=current_user.id,
        durum="beklemede",
        notlar=f"[STOK YOK - ÖN SİPARİŞ] {notlar}",
        olusturma_tarihi=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    db.session.add(talep)
    db.session.flush()

    kalem = SiparisTalebiKalemi(
        talep_id=talep.id,
        urun_id=urun_id,
        miktar=miktar,
    )
    db.session.add(kalem)
    db.session.commit()
    flash("Ön sipariş talebiniz oluşturuldu.", "success")
    return redirect(url_for("magaza.stok_gorunum"))


# ---------------------------------------------------------------------------
# 5. Sipariş iptal (stok sayfasından)
# ---------------------------------------------------------------------------
@magaza_bp.route("/stok/iptal/<int:talep_id>", methods=["POST"])
@magaza_required
def siparis_iptal(talep_id):
    talep = SiparisTalebi.query.get_or_404(talep_id)

    if talep.magaza_id != current_user.magaza_id:
        flash("Bu talep size ait değil.", "danger")
        return redirect(url_for("magaza.stok_gorunum"))

    if talep.durum not in ("beklemede",):
        flash("Yalnızca beklemede durumundaki talepler iptal edilebilir.", "warning")
        return redirect(url_for("magaza.stok_gorunum"))

    sebep = request.form.get("iptal_sebebi", "").strip()
    if not sebep:
        flash("İptal sebebi zorunludur.", "warning")
        return redirect(url_for("magaza.stok_gorunum"))

    talep.durum = "iptal"
    talep.iptal_sebebi = sebep
    db.session.commit()
    flash("Talep iptal edildi.", "success")
    return redirect(url_for("magaza.stok_gorunum"))


# ---------------------------------------------------------------------------
# 6. Talep oluştur
# ---------------------------------------------------------------------------
@magaza_bp.route("/talep", methods=["GET", "POST"])
@magaza_required
@yetki_gerekli("talepler")
def talep_olustur():
    urunler = Urun.query.order_by(Urun.ad).all()

    if request.method == "GET":
        return render_template("magaza/talep.html", urunler=urunler)

    notlar = request.form.get("notlar", "").strip()
    urun_ids = request.form.getlist("urun_ids[]")
    miktarlar = request.form.getlist("miktarlar[]")

    kalemler = []
    for uid, mik in zip(urun_ids, miktarlar):
        try:
            uid = int(uid)
            mik = float(mik)
            if mik > 0:
                kalemler.append((uid, mik))
        except (ValueError, TypeError):
            continue

    if not kalemler:
        flash("En az bir ürün ve miktar girmelisiniz.", "warning")
        return render_template("magaza/talep.html", urunler=urunler)

    talep = SiparisTalebi(
        magaza_id=current_user.magaza_id,
        olusturan_id=current_user.id,
        durum="beklemede",
        notlar=notlar,
        olusturma_tarihi=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    db.session.add(talep)
    db.session.flush()

    for urun_id, miktar in kalemler:
        kalem = SiparisTalebiKalemi(
            talep_id=talep.id,
            urun_id=urun_id,
            miktar=miktar,
        )
        db.session.add(kalem)

    db.session.commit()
    flash("Sipariş talebiniz oluşturuldu.", "success")
    return redirect(url_for("magaza.taleplerim"))


# ---------------------------------------------------------------------------
# 7. Taleplerim
# ---------------------------------------------------------------------------
@magaza_bp.route("/taleplerim")
@magaza_required
@yetki_gerekli("talepler")
def taleplerim():
    talepler = (
        SiparisTalebi.query
        .filter_by(magaza_id=current_user.magaza_id)
        .order_by(SiparisTalebi.id.desc())
        .all()
    )
    return render_template("magaza/taleplerim.html", talepler=talepler)


# ---------------------------------------------------------------------------
# 8. Talep iptal (taleplerim sayfasından)
# ---------------------------------------------------------------------------
@magaza_bp.route("/taleplerim/<int:id>/iptal", methods=["POST"])
@magaza_required
def talep_iptal(id):
    talep = SiparisTalebi.query.get_or_404(id)

    if talep.magaza_id != current_user.magaza_id:
        flash("Bu talep size ait değil.", "danger")
        return redirect(url_for("magaza.taleplerim"))

    if talep.durum not in ("beklemede", "onaylandi"):
        flash("Bu talep artık iptal edilemez.", "warning")
        return redirect(url_for("magaza.taleplerim"))

    sebep = request.form.get("iptal_sebebi", "").strip()
    if not sebep:
        flash("İptal sebebi zorunludur.", "warning")
        return redirect(url_for("magaza.taleplerim"))

    talep.durum = "iptal"
    talep.iptal_sebebi = sebep
    db.session.commit()
    flash("Talep iptal edildi.", "success")
    return redirect(url_for("magaza.taleplerim"))


# ---------------------------------------------------------------------------
# 9. Sevklerim
# ---------------------------------------------------------------------------
@magaza_bp.route("/sevklerim")
@magaza_required
@yetki_gerekli("sevklerim")
def sevklerim():
    sevkler = (
        Sevk.query
        .filter_by(magaza_id=current_user.magaza_id)
        .order_by(Sevk.id.desc())
        .all()
    )
    return render_template("magaza/sevklerim.html", sevkler=sevkler)


# ---------------------------------------------------------------------------
# 10. Teslim al
# ---------------------------------------------------------------------------
@magaza_bp.route("/sevklerim/<int:sevk_id>/teslim-al", methods=["POST"])
@magaza_required
def teslim_al(sevk_id):
    sevk = Sevk.query.get_or_404(sevk_id)

    if sevk.magaza_id != current_user.magaza_id:
        flash("Bu sevk size ait değil.", "danger")
        return redirect(url_for("magaza.sevklerim"))

    if sevk.teslim_durumu == "teslim_alindi":
        flash("Bu sevk zaten teslim alınmış.", "warning")
        return redirect(url_for("magaza.sevklerim"))

    sevk.teslim_durumu = "teslim_alindi"
    sevk.teslim_tarihi = datetime.now().strftime("%Y-%m-%d %H:%M")
    db.session.commit()
    flash("Sevk teslim alındı olarak işaretlendi.", "success")
    return redirect(url_for("magaza.sevklerim"))


# ---------------------------------------------------------------------------
# 11. Satış gir
# ---------------------------------------------------------------------------
@magaza_bp.route("/satis", methods=["GET", "POST"])
@magaza_required
@yetki_gerekli("satis")
def satis_gir():
    stok = magaza_stok_ozet(current_user.magaza_id)
    urunler_eldeki = [s for s in stok if s["eldeki"] > 0]

    if request.method == "GET":
        return render_template("magaza/satis.html", urunler_eldeki=urunler_eldeki)

    tarih = request.form.get("tarih", "").strip() or datetime.now().strftime("%Y-%m-%d")
    urun_ids = request.form.getlist("urun_ids[]")
    miktarlar = request.form.getlist("miktarlar[]")
    notlar_list = request.form.getlist("notlar_list[]")

    # Stok map for quick lookup
    stok_map = {s["urun"].id: s["eldeki"] for s in stok}

    yeni_satirlar = []
    for i, (uid, mik) in enumerate(zip(urun_ids, miktarlar)):
        try:
            uid = int(uid)
            mik = float(mik)
        except (ValueError, TypeError):
            continue
        if mik <= 0:
            continue
        eldeki = stok_map.get(uid, 0)
        if eldeki < mik:
            urun = Urun.query.get(uid)
            ad = urun.ad if urun else str(uid)
            flash(f"'{ad}' için yeterli stok yok. Eldeki: {eldeki}, Girilen: {mik}", "danger")
            return render_template("magaza/satis.html", urunler_eldeki=urunler_eldeki)
        not_metni = notlar_list[i] if i < len(notlar_list) else ""
        yeni_satirlar.append((uid, mik, not_metni))

    for urun_id, miktar, not_metni in yeni_satirlar:
        hareket = SatisHareketi(
            magaza_id=current_user.magaza_id,
            urun_id=urun_id,
            miktar=miktar,
            tarih=tarih,
            notlar=not_metni,
            kaydeden_id=current_user.id,
        )
        db.session.add(hareket)

    db.session.commit()
    flash(f"{len(yeni_satirlar)} satış kaydedildi.", "success")
    return redirect(url_for("magaza.satis_gecmis"))


# ---------------------------------------------------------------------------
# 12. Satış geçmiş
# ---------------------------------------------------------------------------
@magaza_bp.route("/satis/gecmis")
@magaza_required
@yetki_gerekli("satis")
def satis_gecmis():
    satirlar = (
        SatisHareketi.query
        .filter_by(magaza_id=current_user.magaza_id)
        .order_by(SatisHareketi.id.desc())
        .all()
    )
    return render_template("magaza/satis_gecmis.html", satirlar=satirlar)


# ---------------------------------------------------------------------------
# 13. SSH bildir
# ---------------------------------------------------------------------------
@magaza_bp.route("/ssh", methods=["GET", "POST"])
@magaza_required
@yetki_gerekli("ssh")
def ssh_bildir():
    urunler = Urun.query.order_by(Urun.ad).all()

    if request.method == "GET":
        return render_template("magaza/ssh.html", urunler=urunler)

    urun_id = request.form.get("urun_id", type=int)
    paket_id = request.form.get("paket_id", type=int)
    hasar_aciklamasi = request.form.get("hasar_aciklamasi", "").strip()
    talep_miktar = request.form.get("talep_miktar", type=float)

    if not urun_id or not hasar_aciklamasi:
        flash("Ürün ve hasar açıklaması zorunludur.", "warning")
        return render_template("magaza/ssh.html", urunler=urunler)

    bildirim = SshBildirimi(
        magaza_id=current_user.magaza_id,
        urun_id=urun_id,
        paket_id=paket_id,
        hasar_aciklamasi=hasar_aciklamasi,
        talep_miktar=talep_miktar,
        durum="beklemede",
        bildirim_tarihi=datetime.now().strftime("%Y-%m-%d %H:%M"),
        bildiren_id=current_user.id,
    )
    db.session.add(bildirim)
    db.session.commit()
    flash("SSH bildirimi oluşturuldu.", "success")
    return redirect(url_for("magaza.ssh_gecmis"))


# ---------------------------------------------------------------------------
# 14. SSH geçmiş
# ---------------------------------------------------------------------------
@magaza_bp.route("/ssh/gecmis")
@magaza_required
@yetki_gerekli("ssh")
def ssh_gecmis():
    bildirimleri = (
        SshBildirimi.query
        .filter_by(magaza_id=current_user.magaza_id)
        .order_by(SshBildirimi.id.desc())
        .all()
    )
    return render_template("magaza/ssh_gecmis.html", bildirimleri=bildirimleri)


# ---------------------------------------------------------------------------
# 15. API: ürün paketleri
# ---------------------------------------------------------------------------
@magaza_bp.route("/api/urun-paketleri/<int:urun_id>")
@login_required
def urun_paketleri(urun_id):
    paketler = (
        UrunPaketi.query
        .filter_by(urun_id=urun_id)
        .order_by(UrunPaketi.paket_no)
        .all()
    )
    return jsonify([
        {"id": p.id, "ad": f"Paket {p.paket_no} - {p.paket_adi}"}
        for p in paketler
    ])


# ---------------------------------------------------------------------------
# 16. Katalog
# ---------------------------------------------------------------------------
@magaza_bp.route("/katalog")
@magaza_required
@yetki_gerekli("katalog")
def katalog():
    magaza_id = current_user.magaza_id
    fiyat_yetkisi = current_user.yetkisi_var_mi("katalog_fiyat")

    tum_urunler = KatalogUrun.query.filter_by(aktif=True).all()
    gorunen = []

    for ku in tum_urunler:
        if ku.gorunurluk == "herkes":
            fiyat_gorunsun = fiyat_yetkisi and bool(ku.fiyat_onaylandi)
            gorunen.append((ku, fiyat_gorunsun))
        elif ku.gorunurluk == "secili":
            izin = KatalogMagazaIzin.query.filter_by(
                katalog_urun_id=ku.id,
                magaza_id=magaza_id
            ).first()
            if izin:
                fiyat_gorunsun = fiyat_yetkisi and bool(izin.fiyat_gorunsun) and bool(ku.fiyat_onaylandi)
                gorunen.append((ku, fiyat_gorunsun))
        # "gizli": skip

    return render_template("magaza/katalog.html", urunler=gorunen)


# ---------------------------------------------------------------------------
# 17. Katalog detay
# ---------------------------------------------------------------------------
@magaza_bp.route("/katalog/<int:id>")
@magaza_required
@yetki_gerekli("katalog")
def katalog_detay(id):
    ku = KatalogUrun.query.get_or_404(id)

    if ku.gorunurluk == "gizli":
        abort(403)

    magaza_id = current_user.magaza_id
    fiyat_yetkisi = current_user.yetkisi_var_mi("katalog_fiyat")

    if ku.gorunurluk == "herkes":
        fiyat_gorunsun = fiyat_yetkisi and bool(ku.fiyat_onaylandi)
    elif ku.gorunurluk == "secili":
        izin = KatalogMagazaIzin.query.filter_by(
            katalog_urun_id=ku.id,
            magaza_id=magaza_id
        ).first()
        if not izin:
            abort(403)
        fiyat_gorunsun = fiyat_yetkisi and bool(izin.fiyat_gorunsun) and bool(ku.fiyat_onaylandi)
    else:
        abort(403)

    return render_template(
        "magaza/katalog_detay.html",
        ku=ku,
        fiyat_gorunsun=fiyat_gorunsun,
    )


# ---------------------------------------------------------------------------
# 18. Katalog sipariş
# ---------------------------------------------------------------------------
@magaza_bp.route("/katalog/siparis", methods=["POST"])
@magaza_required
@yetki_gerekli("katalog")
def katalog_siparis():
    katalog_urun_id = request.form.get("katalog_urun_id", type=int)
    miktar = request.form.get("miktar", type=float)
    notlar = request.form.get("notlar", "").strip()

    ku = KatalogUrun.query.get_or_404(katalog_urun_id)

    # Ürün adına göre eşleşen Urun bul (büyük/küçük harf duyarsız)
    urun = Urun.query.filter(
        db.func.lower(Urun.ad) == db.func.lower(ku.ad)
    ).first()

    if urun:
        talep = SiparisTalebi(
            magaza_id=current_user.magaza_id,
            olusturan_id=current_user.id,
            durum="beklemede",
            notlar=notlar,
            olusturma_tarihi=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        db.session.add(talep)
        db.session.flush()

        kalem = SiparisTalebiKalemi(
            talep_id=talep.id,
            urun_id=urun.id,
            miktar=miktar,
        )
        db.session.add(kalem)
    else:
        talep = SiparisTalebi(
            magaza_id=current_user.magaza_id,
            olusturan_id=current_user.id,
            durum="beklemede",
            notlar=f"[Katalog: {ku.ad}] {notlar}",
            olusturma_tarihi=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        db.session.add(talep)

    db.session.commit()
    flash("Katalog siparişiniz oluşturuldu.", "success")
    return redirect(url_for("magaza.katalog"))


# ---------------------------------------------------------------------------
# 19. Fiyat teklifi gönder
# ---------------------------------------------------------------------------
@magaza_bp.route("/katalog/fiyat-teklifi", methods=["POST"])
@magaza_required
@yetki_gerekli("katalog")
def fiyat_teklifi_gonder():
    katalog_urun_id = request.form.get("katalog_urun_id", type=int)
    miktar = request.form.get("miktar", type=float)
    not_ = request.form.get("not_", "").strip()

    teklif = FiyatTeklifi(
        magaza_id=current_user.magaza_id,
        katalog_urun_id=katalog_urun_id,
        miktar=miktar,
        not_=not_,
        olusturan_id=current_user.id,
        durum="beklemede",
        olusturma_tarihi=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    db.session.add(teklif)
    db.session.commit()
    flash("Fiyat teklifiniz gönderildi.", "success")
    return redirect(url_for("magaza.katalog"))
