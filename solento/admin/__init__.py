from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, send_file
from flask_login import login_required, current_user
from functools import wraps
from models import (db, Sehir, Magaza, Urun, UrunPaketi, Siparis, UretimPaketGirisi,
                    StokHareketi, Sevk, SevkKalemi, GenelGider,
                    SiparisTalebi, SiparisTalebiKalemi, SshBildirimi, Kullanici, KullaniciYetki,
                    KatalogUrun, KatalogResim, KatalogMagazaIzin, SatisHareketi, FiyatTeklifi)
from datetime import datetime
import os, uuid, io
from werkzeug.utils import secure_filename

admin_bp = Blueprint("admin", __name__)

IZINLI_UZANTILAR = {"jpg", "jpeg", "png", "webp", "gif"}
GIRIS_TURLERI = ["uretim_giris", "duzeltme_giris", "iade"]
CIKIS_TURLERI = ["sevk_cikis", "duzeltme_cikis", "fire"]


# ─── DECORATOR ────────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash("Bu sayfaya erişim yetkiniz yok.", "danger")
            return redirect(url_for("magaza.dashboard"))
        return f(*args, **kwargs)
    return decorated


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def izin_verilen(dosya_adi):
    return "." in dosya_adi and dosya_adi.rsplit(".", 1)[1].lower() in IZINLI_UZANTILAR


def _stok_ozet():
    from sqlalchemy import func as sqlfunc
    urunler = Urun.query.order_by(Urun.ad).all()
    ozet = []
    for u in urunler:
        giris = db.session.query(
            sqlfunc.coalesce(sqlfunc.sum(StokHareketi.miktar), 0)
        ).filter(
            StokHareketi.urun_id == u.id,
            StokHareketi.hareket_turu.in_(GIRIS_TURLERI)
        ).scalar() or 0
        cikis = db.session.query(
            sqlfunc.coalesce(sqlfunc.sum(sqlfunc.abs(StokHareketi.miktar)), 0)
        ).filter(
            StokHareketi.urun_id == u.id,
            StokHareketi.hareket_turu.in_(CIKIS_TURLERI)
        ).scalar() or 0
        bakiye = float(giris) - float(cikis)
        ozet.append({"urun": u, "bakiye": bakiye})
    return ozet


def _rezerve_ozet():
    sonuc = {}
    kalemler = (
        db.session.query(SiparisTalebiKalemi)
        .join(SiparisTalebi, SiparisTalebiKalemi.talep_id == SiparisTalebi.id)
        .filter(SiparisTalebi.durum.in_(["beklemede", "onaylandi"]))
        .all()
    )
    for k in kalemler:
        sonuc[k.urun_id] = sonuc.get(k.urun_id, 0) + k.miktar
    return sonuc


def _urun_bul_veya_olustur(ad):
    import re
    ad_temiz = ad.strip().upper()
    urun = Urun.query.filter(db.func.upper(Urun.ad) == ad_temiz).first()
    if not urun:
        kod = re.sub(r"[^A-Z0-9]", "_", ad_temiz)[:20]
        sayac = Urun.query.filter(Urun.kod.like(f"{kod}%")).count()
        if sayac:
            kod = f"{kod}_{sayac}"
        urun = Urun(kod=kod, ad=ad.strip(), birim="takim")
        db.session.add(urun)
        db.session.flush()
    return urun


def _uretim_stok_gir(siparis):
    sevk_edilebilir = siparis.sevk_edilebilir_takim()
    mevcut = StokHareketi.query.filter_by(
        kaynak="uretim", referans_id=siparis.id, hareket_turu="uretim_giris"
    ).first()
    if mevcut:
        mevcut.miktar = sevk_edilebilir
        mevcut.tarih = datetime.now().strftime("%Y-%m-%d")
    elif sevk_edilebilir > 0:
        db.session.add(StokHareketi(
            tarih=datetime.now().strftime("%Y-%m-%d"),
            urun_id=siparis.urun_id,
            hareket_turu="uretim_giris",
            miktar=sevk_edilebilir,
            kaynak="uretim",
            referans_id=siparis.id,
            depo="ana_depo"
        ))
    db.session.commit()


def _auto_complete_siparisler(urun_id):
    from sqlalchemy import func as sqlfunc
    giris = db.session.query(
        sqlfunc.coalesce(sqlfunc.sum(StokHareketi.miktar), 0)
    ).filter(
        StokHareketi.urun_id == urun_id,
        StokHareketi.hareket_turu.in_(GIRIS_TURLERI)
    ).scalar() or 0
    cikis = db.session.query(
        sqlfunc.coalesce(sqlfunc.sum(sqlfunc.abs(StokHareketi.miktar)), 0)
    ).filter(
        StokHareketi.urun_id == urun_id,
        StokHareketi.hareket_turu.in_(CIKIS_TURLERI)
    ).scalar() or 0
    if float(giris) - float(cikis) <= 0:
        for sp in Siparis.query.filter_by(urun_id=urun_id).filter(
            Siparis.durum.in_(["hazir", "uretimde"])
        ).all():
            sp.durum = "tamamlandi"
            for g in sp.uretim_girisleri:
                g.uretilen_miktar = 0


# ─── 1. DASHBOARD ─────────────────────────────────────────────────────────────

@admin_bp.route("/")
@admin_required
def dashboard():
    bekleyen_talepler = SiparisTalebi.query.filter_by(durum="beklemede").count()
    acik_ssh = SshBildirimi.query.filter(SshBildirimi.durum != "teslim_edildi").count()
    aktif_magaza = Magaza.query.count()
    aktif_siparisler = (
        Siparis.query
        .filter(Siparis.durum.in_(["uretimde", "hazir"]))
        .order_by(Siparis.id.desc())
        .all()
    )
    for s in aktif_siparisler:
        s._sevk_edilebilir = s.sevk_edilebilir_takim()
        s._eksikler = s.eksik_paketler()
    son_sevkler = Sevk.query.order_by(Sevk.id.desc()).limit(5).all()
    stok_ozet = [item for item in _stok_ozet() if item["bakiye"] > 0]
    return render_template(
        "admin/dashboard.html",
        bekleyen_talepler=bekleyen_talepler,
        acik_ssh=acik_ssh,
        aktif_magaza=aktif_magaza,
        aktif_siparisler=aktif_siparisler,
        son_sevkler=son_sevkler,
        stok_ozet=stok_ozet
    )


# ─── 2. STOK ──────────────────────────────────────────────────────────────────

@admin_bp.route("/stok")
@admin_required
def stok():
    arama = request.args.get("arama", "").strip()
    ozet = _stok_ozet()
    rezerve = _rezerve_ozet()
    for item in ozet:
        uid = item["urun"].id
        item["rezerve"] = rezerve.get(uid, 0)
        item["kullanilabilir"] = item["bakiye"] - item["rezerve"]
    if arama:
        arama_lower = arama.lower()
        ozet = [item for item in ozet if arama_lower in item["urun"].ad.lower()]
    hareketler = StokHareketi.query.order_by(StokHareketi.id.desc()).limit(100).all()
    urunler = Urun.query.order_by(Urun.ad).all()
    return render_template(
        "admin/stok.html",
        ozet=ozet,
        hareketler=hareketler,
        urunler=urunler,
        arama=arama
    )


# ─── 3. STOK MANUEL GİRİŞ ────────────────────────────────────────────────────

@admin_bp.route("/stok/manuel-giris", methods=["POST"])
@admin_required
def stok_manuel_giris():
    urun_id = request.form.get("urun_id", type=int)
    miktar = request.form.get("miktar", type=float)
    hareket_turu = request.form.get("hareket_turu", "duzeltme_giris")
    aciklama = request.form.get("aciklama", "").strip()
    if urun_id and miktar and miktar != 0:
        h = StokHareketi(
            tarih=datetime.now().strftime("%Y-%m-%d"),
            urun_id=urun_id,
            hareket_turu=hareket_turu,
            miktar=miktar,
            kaynak=f"Manuel: {aciklama}" if aciklama else "Manuel",
            depo="ana_depo"
        )
        db.session.add(h)
        db.session.commit()
        flash(f"Manuel stok hareketi eklendi: {miktar:+g} adet.", "success")
    else:
        flash("Ürün ve miktar zorunludur.", "warning")
    return redirect(url_for("admin.stok"))


# ─── 4. STOK HAREKET SİL ──────────────────────────────────────────────────────

@admin_bp.route("/stok/hareket/<int:id>/sil", methods=["POST"])
@admin_required
def stok_hareket_sil(id):
    h = StokHareketi.query.get_or_404(id)
    db.session.delete(h)
    db.session.commit()
    flash("Hareket kaydı silindi.", "success")
    return redirect(url_for("admin.stok"))


# ─── 5. STOK HAREKET DÜZENLE ──────────────────────────────────────────────────

@admin_bp.route("/stok/hareket/<int:id>/duzenle", methods=["POST"])
@admin_required
def stok_hareket_duzenle(id):
    h = StokHareketi.query.get_or_404(id)
    yeni_miktar = request.form.get("miktar", type=float)
    aciklama = request.form.get("aciklama", "").strip()
    if yeni_miktar is not None:
        h.miktar = yeni_miktar
    if aciklama:
        h.kaynak = aciklama
    db.session.commit()
    flash("Hareket kaydı güncellendi.", "success")
    return redirect(url_for("admin.stok"))


# ─── 6. ÜRETİM ────────────────────────────────────────────────────────────────

@admin_bp.route("/uretim")
@admin_required
def uretim():
    durum_filter = request.args.get("durum", "aktif")
    q = Siparis.query
    if durum_filter == "aktif":
        q = q.filter(Siparis.durum.in_(["uretimde", "hazir"]))
    elif durum_filter == "tamamlandi":
        q = q.filter_by(durum="tamamlandi")
    siparisler = q.order_by(Siparis.id.desc()).all()
    for s in siparisler:
        s._sevk_edilebilir = s.sevk_edilebilir_takim()
        s._eksikler = s.eksik_paketler()
    urunler = Urun.query.order_by(Urun.ad).all()
    aktif_sayi = Siparis.query.filter(Siparis.durum.in_(["uretimde", "hazir"])).count()
    tamamlandi_sayi = Siparis.query.filter_by(durum="tamamlandi").count()
    return render_template(
        "admin/uretim.html",
        siparisler=siparisler,
        urunler=urunler,
        now=datetime.now(),
        durum_filter=durum_filter,
        aktif_sayi=aktif_sayi,
        tamamlandi_sayi=tamamlandi_sayi
    )


# ─── 7. ÜRETİM YENİ ───────────────────────────────────────────────────────────

@admin_bp.route("/uretim/yeni", methods=["POST"])
@admin_required
def uretim_yeni():
    urun_id = request.form.get("urun_id", type=int)
    urun_ad = request.form.get("urun_ad", "").strip()
    paket_sayisi = request.form.get("paket_sayisi", type=int, default=1)
    siparis_adeti = request.form.get("siparis_adeti", type=float)
    notlar = request.form.get("notlar", "").strip()
    tarih = request.form.get("tarih", datetime.now().strftime("%Y-%m-%d"))

    if not siparis_adeti:
        flash("Sipariş adeti zorunludur.", "warning")
        return redirect(url_for("admin.uretim"))

    if urun_id:
        urun = Urun.query.get_or_404(urun_id)
    else:
        if not urun_ad:
            flash("Ürün adı veya ürün seçimi zorunludur.", "warning")
            return redirect(url_for("admin.uretim"))
        urun = _urun_bul_veya_olustur(urun_ad)

    mevcut_paket = len(urun.paketler)
    if mevcut_paket < paket_sayisi:
        for i in range(mevcut_paket + 1, paket_sayisi + 1):
            db.session.add(UrunPaketi(urun_id=urun.id, paket_no=i, paket_adi=f"Paket {i}"))
        db.session.flush()

    s = Siparis(tarih=tarih, urun_id=urun.id, siparis_adeti=siparis_adeti, notlar=notlar)
    db.session.add(s)
    db.session.commit()
    flash("Sipariş oluşturuldu. Paket miktarlarını girin.", "success")
    return redirect(url_for("admin.paket_gir", id=s.id))


# ─── 8. PAKET GİR ─────────────────────────────────────────────────────────────

@admin_bp.route("/uretim/<int:id>/paket-gir", methods=["GET", "POST"])
@admin_required
def paket_gir(id):
    siparis = Siparis.query.get_or_404(id)
    if request.method == "POST":
        for p in siparis.urun.paketler:
            miktar = request.form.get(f"paket_{p.id}", type=float, default=0)
            mevcut = UretimPaketGirisi.query.filter_by(siparis_id=id, paket_id=p.id).first()
            if mevcut:
                mevcut.uretilen_miktar = miktar
                mevcut.guncelleme_tarihi = datetime.now().strftime("%Y-%m-%d %H:%M")
            else:
                db.session.add(UretimPaketGirisi(
                    siparis_id=id, paket_id=p.id, uretilen_miktar=miktar
                ))
        sevk_edilebilir = siparis.sevk_edilebilir_takim()
        siparis.durum = "hazir" if sevk_edilebilir >= siparis.siparis_adeti else "uretimde"
        db.session.commit()
        _uretim_stok_gir(siparis)
        flash("Paket miktarları kaydedildi.", "success")
        return redirect(url_for("admin.uretim"))
    mevcut = {g.paket_id: g.uretilen_miktar for g in siparis.uretim_girisleri}
    return render_template("admin/paket_gir.html", siparis=siparis, mevcut=mevcut)


# ─── 9. ÜRETİM TAMAMLA ────────────────────────────────────────────────────────

@admin_bp.route("/uretim/<int:id>/tamamla", methods=["POST"])
@admin_required
def uretim_tamamla(id):
    siparis = Siparis.query.get_or_404(id)
    siparis.durum = "tamamlandi"
    db.session.commit()
    flash(f"'{siparis.urun.ad}' üretim siparişi tamamlandı.", "success")
    return redirect(url_for("admin.uretim"))


# ─── 10. ÜRETİM TEKRAR AÇ ─────────────────────────────────────────────────────

@admin_bp.route("/uretim/<int:id>/tekrar-ac", methods=["POST"])
@admin_required
def uretim_tekrar_ac(id):
    siparis = Siparis.query.get_or_404(id)
    siparis.durum = "uretimde"
    db.session.commit()
    flash("Sipariş tekrar aktife alındı.", "success")
    return redirect(url_for("admin.uretim", durum="aktif"))


# ─── 11. ÜRETİM SİL ───────────────────────────────────────────────────────────

@admin_bp.route("/uretim/<int:id>/sil", methods=["POST"])
@admin_required
def uretim_sil(id):
    siparis = Siparis.query.get_or_404(id)
    StokHareketi.query.filter_by(kaynak="uretim", referans_id=id).delete()
    UretimPaketGirisi.query.filter_by(siparis_id=id).delete()
    db.session.delete(siparis)
    db.session.commit()
    flash("Üretim siparişi silindi.", "success")
    return redirect(url_for("admin.uretim"))


# ─── 12. PAKET DÜZENLE ────────────────────────────────────────────────────────

@admin_bp.route("/tanimlar/urun/<int:id>/paket-duzenle", methods=["GET", "POST"])
@admin_required
def paket_duzenle(id):
    urun = Urun.query.get_or_404(id)
    if request.method == "POST":
        for p in urun.paketler:
            yeni_ad = request.form.get(f"paket_{p.id}", "").strip()
            if yeni_ad:
                p.paket_adi = yeni_ad
        db.session.commit()
        flash("Paket adları güncellendi.", "success")
        return redirect(url_for("admin.tanimlar"))
    return render_template("admin/paket_duzenle.html", urun=urun)


# ─── 13. TALEPLER ─────────────────────────────────────────────────────────────

@admin_bp.route("/talepler")
@admin_required
def talepler():
    durum_filter = request.args.get("durum", "beklemede")
    q = SiparisTalebi.query
    if durum_filter != "tumu":
        q = q.filter_by(durum=durum_filter)
    talepler_listesi = q.order_by(SiparisTalebi.id.desc()).all()
    stok = {item["urun"].id: item["bakiye"] for item in _stok_ozet()}
    rezerve = _rezerve_ozet()
    kullanilabilir = {
        uid: stok.get(uid, 0) - rezerve.get(uid, 0)
        for uid in set(list(stok.keys()) + list(rezerve.keys()))
    }
    beklemede_sayi = SiparisTalebi.query.filter_by(durum="beklemede").count()
    return render_template(
        "admin/talepler.html",
        talepler=talepler_listesi,
        durum_filter=durum_filter,
        stok=stok,
        rezerve=rezerve,
        kullanilabilir=kullanilabilir,
        beklemede_sayi=beklemede_sayi
    )


# ─── 14. TALEP ONAYLA ─────────────────────────────────────────────────────────

@admin_bp.route("/talepler/<int:id>/onayla", methods=["POST"])
@admin_required
def talep_onayla(id):
    t = SiparisTalebi.query.get_or_404(id)
    t.durum = "onaylandi"
    db.session.commit()
    flash("Talep onaylandı.", "success")
    return redirect(url_for("admin.talepler"))


# ─── 15. TALEP REDDET ─────────────────────────────────────────────────────────

@admin_bp.route("/talepler/<int:id>/reddet", methods=["POST"])
@admin_required
def talep_reddet(id):
    t = SiparisTalebi.query.get_or_404(id)
    t.durum = "iptal"
    db.session.commit()
    flash("Talep reddedildi.", "warning")
    return redirect(url_for("admin.talepler"))


# ─── 16. SEVKİYAT HIZLI ───────────────────────────────────────────────────────

@admin_bp.route("/talepler/<int:id>/sevk-hizli", methods=["POST"])
@admin_required
def sevk_hizli(id):
    talep = SiparisTalebi.query.get_or_404(id)
    tarih = datetime.now().strftime("%Y-%m-%d")
    nakliye_ucreti = request.form.get("nakliye_ucreti", type=float, default=0)
    iscilik = request.form.get("iscilik", type=float, default=0)
    kdv_oran = request.form.get("kdv_oran", type=int, default=0)
    notlar = request.form.get("notlar", "").strip()
    nakliye_goster = bool(request.form.get("nakliye_goster"))

    sevk = Sevk(
        tarih=tarih,
        magaza_id=talep.magaza_id,
        talep_id=id,
        nakliye_ucreti=nakliye_ucreti,
        iscilik=iscilik,
        kdv_oran=kdv_oran,
        notlar=notlar,
        nakliye_goster=nakliye_goster
    )
    db.session.add(sevk)
    db.session.flush()

    for k in talep.kalemler:
        db.session.add(SevkKalemi(sevk_id=sevk.id, urun_id=k.urun_id, miktar=k.miktar))
        db.session.add(StokHareketi(
            tarih=tarih,
            urun_id=k.urun_id,
            hareket_turu="sevk_cikis",
            miktar=k.miktar,
            kaynak="sevk",
            referans_id=sevk.id,
            depo="ana_depo"
        ))

    talep.durum = "sevk_edildi"
    db.session.commit()

    for k in talep.kalemler:
        _auto_complete_siparisler(k.urun_id)
    db.session.commit()

    flash(f"Sevk oluşturuldu (#{sevk.id}).", "success")
    return redirect(url_for("admin.talepler"))


# ─── 17. TALEP İPTAL ──────────────────────────────────────────────────────────

@admin_bp.route("/talepler/<int:id>/iptal", methods=["POST"])
@admin_required
def talep_iptal(id):
    talep = SiparisTalebi.query.get_or_404(id)
    sebep = request.form.get("sebep", "").strip()
    if not sebep:
        flash("İptal sebebi girilmesi zorunludur.", "warning")
        return redirect(url_for("admin.talepler"))
    talep.durum = "iptal"
    talep.iptal_sebebi = sebep
    db.session.commit()
    flash(f"Talep #{id} iptal edildi.", "warning")
    return redirect(url_for("admin.talepler"))


# ─── 18. SEVKİYAT LİSTESİ ────────────────────────────────────────────────────

@admin_bp.route("/sevk")
@admin_required
def sevk_listesi():
    sevkler = Sevk.query.order_by(Sevk.id.desc()).all()
    magazalar = Magaza.query.join(Sehir).order_by(Sehir.ad, Magaza.ad).all()
    urunler = Urun.query.order_by(Urun.ad).all()
    talep_id = request.args.get("talep_id", type=int)
    talep = SiparisTalebi.query.get(talep_id) if talep_id else None
    return render_template(
        "admin/sevk.html",
        sevkler=sevkler,
        magazalar=magazalar,
        urunler=urunler,
        now=datetime.now(),
        talep=talep
    )


# ─── 19. SEVKİYAT YENİ ────────────────────────────────────────────────────────

@admin_bp.route("/sevk/yeni", methods=["POST"])
@admin_required
def sevk_yeni():
    alici_turu = request.form.get("alici_turu", "magaza")
    magaza_id = request.form.get("magaza_id", type=int)
    alici_adi = request.form.get("alici_adi", "").strip()
    tarih = request.form.get("tarih", datetime.now().strftime("%Y-%m-%d"))
    nakliye_ucreti = request.form.get("nakliye_ucreti", type=float, default=0)
    iscilik = request.form.get("iscilik", type=float, default=0)
    kdv_oran = request.form.get("kdv_oran", type=int, default=0)
    notlar = request.form.get("notlar", "").strip()
    talep_id = request.form.get("talep_id", type=int)

    if alici_turu == "serbest":
        magaza_id = None
        if not alici_adi:
            flash("Serbest alıcı adı giriniz.", "warning")
            return redirect(url_for("admin.sevk_listesi"))

    urun_ids = request.form.getlist("urun_ids[]")
    miktarlar = request.form.getlist("miktarlar[]")
    gider_tur = request.form.getlist("gider_tur[]")
    gider_tutar = request.form.getlist("gider_tutar[]")
    gider_aciklama = request.form.getlist("gider_aciklama[]")

    kalemler = []
    for uid_s, mkt_s in zip(urun_ids, miktarlar):
        try:
            mkt_f = float(mkt_s or 0)
        except ValueError:
            continue
        if not uid_s or mkt_f <= 0:
            continue
        urun = Urun.query.get(int(uid_s))
        if urun:
            kalemler.append((urun, mkt_f))

    if (alici_turu == "magaza" and not magaza_id) or not kalemler:
        flash("Mağaza ve en az bir ürün girilmesi zorunludur.", "warning")
        return redirect(url_for("admin.sevk_listesi"))

    sevk = Sevk(
        tarih=tarih,
        magaza_id=magaza_id,
        nakliye_ucreti=nakliye_ucreti,
        iscilik=iscilik,
        kdv_oran=kdv_oran,
        notlar=notlar,
        talep_id=talep_id,
        alici_turu=alici_turu,
        alici_adi=alici_adi if alici_turu == "serbest" else None
    )
    db.session.add(sevk)
    db.session.flush()

    for urun, mkt in kalemler:
        db.session.add(SevkKalemi(sevk_id=sevk.id, urun_id=urun.id, miktar=mkt))
        db.session.add(StokHareketi(
            tarih=tarih,
            urun_id=urun.id,
            hareket_turu="sevk_cikis",
            miktar=mkt,
            kaynak="sevk",
            referans_id=sevk.id,
            depo="ana_depo"
        ))

    for tur, tutar_s, aciklama in zip(gider_tur, gider_tutar, gider_aciklama):
        try:
            tutar_f = float(tutar_s or 0)
        except ValueError:
            tutar_f = 0
        if tur and tutar_f > 0:
            db.session.add(GenelGider(
                sevk_id=sevk.id, gider_turu=tur, tutar=tutar_f, aciklama=aciklama
            ))

    if talep_id:
        t = SiparisTalebi.query.get(talep_id)
        if t:
            t.durum = "sevk_edildi"

    db.session.commit()

    for urun, _ in kalemler:
        _auto_complete_siparisler(urun.id)
    db.session.commit()

    flash("Sevk kaydedildi.", "success")
    return redirect(url_for("admin.sevk_listesi"))


# ─── 20. SEVKİYAT SİL ─────────────────────────────────────────────────────────

@admin_bp.route("/sevk/<int:id>/sil", methods=["POST"])
@admin_required
def sevk_sil(id):
    sevk = Sevk.query.get_or_404(id)
    StokHareketi.query.filter_by(kaynak="sevk", referans_id=id).delete()
    db.session.delete(sevk)
    db.session.commit()
    flash("Sevk silindi.", "success")
    return redirect(url_for("admin.sevk_listesi"))


# ─── 21. SEVKİYAT DÜZENLE ─────────────────────────────────────────────────────

@admin_bp.route("/sevk/<int:id>/duzenle", methods=["POST"])
@admin_required
def sevk_duzenle(id):
    sevk = Sevk.query.get_or_404(id)
    sevk.nakliye_ucreti = request.form.get("nakliye_ucreti", type=float, default=0)
    sevk.iscilik = request.form.get("iscilik", type=float, default=0)
    sevk.kdv_oran = request.form.get("kdv_oran", type=int, default=0)
    sevk.notlar = request.form.get("notlar", "").strip()
    sevk.nakliye_goster = bool(request.form.get("nakliye_goster"))

    urun_ids = request.form.getlist("urun_ids[]")
    miktarlar = request.form.getlist("miktarlar[]")

    StokHareketi.query.filter_by(referans_id=id, hareket_turu="sevk_cikis").delete()
    SevkKalemi.query.filter_by(sevk_id=id).delete()
    db.session.flush()

    tarih = sevk.tarih
    for uid_s, mkt_s in zip(urun_ids, miktarlar):
        try:
            mkt_f = float(mkt_s or 0)
        except ValueError:
            continue
        if not uid_s or mkt_f <= 0:
            continue
        urun = Urun.query.get(int(uid_s))
        if not urun:
            continue
        db.session.add(SevkKalemi(sevk_id=sevk.id, urun_id=urun.id, miktar=mkt_f))
        db.session.add(StokHareketi(
            tarih=tarih,
            urun_id=urun.id,
            hareket_turu="sevk_cikis",
            miktar=mkt_f,
            kaynak="sevk",
            referans_id=sevk.id,
            depo="ana_depo"
        ))

    db.session.commit()
    flash(f"Sevk #{id} güncellendi.", "success")
    return redirect(url_for("admin.sevk_listesi"))


# ─── 22. SSH LİSTESİ ──────────────────────────────────────────────────────────

@admin_bp.route("/ssh")
@admin_required
def ssh_listesi():
    durum_filter = request.args.get("durum", "")
    q = SshBildirimi.query
    if durum_filter:
        q = q.filter_by(durum=durum_filter)
    bildirimleri = q.order_by(SshBildirimi.id.desc()).all()
    return render_template("admin/ssh.html", bildirimleri=bildirimleri, durum_filter=durum_filter)


# ─── 23. SSH DURUM GÜNCELLE ───────────────────────────────────────────────────

@admin_bp.route("/ssh/<int:id>/durum", methods=["POST"])
@admin_required
def ssh_durum(id):
    b = SshBildirimi.query.get_or_404(id)
    b.durum = request.form.get("durum", b.durum)
    b.admin_notu = request.form.get("admin_notu", b.admin_notu)
    db.session.commit()
    flash("SSH durumu güncellendi.", "success")
    return redirect(url_for("admin.ssh_listesi"))


# ─── 24. MAĞAZA STOK ──────────────────────────────────────────────────────────

@admin_bp.route("/magaza-stok")
@admin_required
def magaza_stok():
    from magaza import magaza_stok_ozet
    secili_magaza_id = request.args.get("magaza_id", type=int)
    secili_sehir_id = request.args.get("sehir_id", type=int)
    magazalar = Magaza.query.join(Sehir).order_by(Sehir.ad, Magaza.ad).all()
    sehirler = Sehir.query.order_by(Sehir.ad).all()
    magaza_ozetleri = []
    for m in magazalar:
        if secili_sehir_id and m.sehir_id != secili_sehir_id:
            continue
        if secili_magaza_id and m.id != secili_magaza_id:
            continue
        stok = magaza_stok_ozet(m.id)
        magaza_ozetleri.append({"magaza": m, "stok": stok})
    return render_template(
        "admin/magaza_stok.html",
        magaza_ozetleri=magaza_ozetleri,
        magazalar=magazalar,
        sehirler=sehirler,
        secili_magaza_id=secili_magaza_id,
        secili_sehir_id=secili_sehir_id
    )


# ─── 25. MAĞAZA SATIŞ ─────────────────────────────────────────────────────────

@admin_bp.route("/magaza-satis")
@admin_required
def magaza_satis():
    sehir_id = request.args.get("sehir_id", type=int)
    magaza_id = request.args.get("magaza_id", type=int)
    tarih_bas = request.args.get("tarih_bas", "")
    tarih_bit = request.args.get("tarih_bit", "")

    q = SatisHareketi.query.join(Magaza).join(Sehir)
    if sehir_id:
        q = q.filter(Magaza.sehir_id == sehir_id)
    if magaza_id:
        q = q.filter(SatisHareketi.magaza_id == magaza_id)
    if tarih_bas:
        q = q.filter(SatisHareketi.tarih >= tarih_bas)
    if tarih_bit:
        q = q.filter(SatisHareketi.tarih <= tarih_bit)
    satirlar = q.order_by(SatisHareketi.id.desc()).all()

    magazalar = Magaza.query.join(Sehir).order_by(Sehir.ad, Magaza.ad).all()
    sehirler = Sehir.query.order_by(Sehir.ad).all()
    return render_template(
        "admin/magaza_satis.html",
        satirlar=satirlar,
        magazalar=magazalar,
        sehirler=sehirler,
        sehir_id=sehir_id,
        magaza_id=magaza_id,
        tarih_bas=tarih_bas,
        tarih_bit=tarih_bit
    )


# ─── 26. TANIMLAR ─────────────────────────────────────────────────────────────

@admin_bp.route("/tanimlar")
@admin_required
def tanimlar():
    sehirler = Sehir.query.order_by(Sehir.ad).all()
    magazalar = Magaza.query.join(Sehir).order_by(Sehir.ad, Magaza.ad).all()
    urunler = Urun.query.order_by(Urun.ad).all()
    return render_template(
        "admin/tanimlar.html",
        sehirler=sehirler,
        magazalar=magazalar,
        urunler=urunler
    )


# ─── 27. ŞEHİR EKLE ───────────────────────────────────────────────────────────

@admin_bp.route("/tanimlar/sehir/ekle", methods=["POST"])
@admin_required
def sehir_ekle():
    ad = request.form.get("ad", "").strip()
    if ad:
        if not Sehir.query.filter_by(ad=ad).first():
            db.session.add(Sehir(ad=ad))
            db.session.commit()
            flash(f"'{ad}' şehri eklendi.", "success")
        else:
            flash("Bu şehir zaten mevcut.", "warning")
    return redirect(url_for("admin.tanimlar"))


# ─── 28. ŞEHİR SİL ────────────────────────────────────────────────────────────

@admin_bp.route("/tanimlar/sehir/sil/<int:id>", methods=["POST"])
@admin_required
def sehir_sil(id):
    s = Sehir.query.get_or_404(id)
    if s.magazalar:
        flash(f"'{s.ad}' şehrinde {len(s.magazalar)} mağaza var. Önce mağazaları silin.", "danger")
        return redirect(url_for("admin.tanimlar"))
    try:
        db.session.delete(s)
        db.session.commit()
        flash(f"'{s.ad}' şehri silindi.", "success")
    except Exception:
        db.session.rollback()
        flash("Şehir silinemedi. Bağlı kayıtlar mevcut.", "danger")
    return redirect(url_for("admin.tanimlar"))


# ─── 29. MAĞAZA EKLE ──────────────────────────────────────────────────────────

@admin_bp.route("/tanimlar/magaza/ekle", methods=["POST"])
@admin_required
def magaza_ekle():
    ad = request.form.get("ad", "").strip()
    sehir_ad = request.form.get("sehir_ad", "").strip().upper()
    adres = request.form.get("adres", "").strip()
    telefon = request.form.get("telefon", "").strip()
    if not ad:
        flash("Mağaza adı zorunludur.", "warning")
        return redirect(url_for("admin.tanimlar"))
    if not sehir_ad:
        flash("Şehir adı zorunludur.", "warning")
        return redirect(url_for("admin.tanimlar"))
    sehir = Sehir.query.filter(db.func.upper(Sehir.ad) == sehir_ad).first()
    if not sehir:
        sehir = Sehir(ad=sehir_ad.title())
        db.session.add(sehir)
        db.session.flush()
    db.session.add(Magaza(ad=ad, sehir_id=sehir.id, adres=adres, telefon=telefon))
    db.session.commit()
    flash(f"'{ad}' mağazası eklendi.", "success")
    return redirect(url_for("admin.tanimlar"))


# ─── 30. MAĞAZA SİL ───────────────────────────────────────────────────────────

@admin_bp.route("/tanimlar/magaza/sil/<int:id>", methods=["POST"])
@admin_required
def magaza_sil(id):
    m = Magaza.query.get_or_404(id)
    if m.kullanicilar:
        flash(f"'{m.ad}' mağazasında {len(m.kullanicilar)} kullanıcı var. Önce kullanıcıları silin.", "danger")
        return redirect(url_for("admin.tanimlar"))
    try:
        db.session.delete(m)
        db.session.commit()
        flash(f"'{m.ad}' mağazası silindi.", "success")
    except Exception:
        db.session.rollback()
        flash("Mağaza silinemedi. Bağlı kayıtlar mevcut.", "danger")
    return redirect(url_for("admin.tanimlar"))


# ─── 31. ÜRÜN EKLE ────────────────────────────────────────────────────────────

@admin_bp.route("/tanimlar/urun/ekle", methods=["POST"])
@admin_required
def urun_ekle():
    import re as _re
    kod = request.form.get("kod", "").strip()
    ad = request.form.get("ad", "").strip()
    birim = request.form.get("birim", "takim").strip()
    paket_sayisi = request.form.get("paket_sayisi", type=int, default=1)
    if not ad:
        flash("Ürün adı zorunludur.", "warning")
        return redirect(url_for("admin.tanimlar"))
    if not kod:
        base = _re.sub(r"[^A-Z0-9]", "_", ad.upper())[:15]
        sayac = Urun.query.filter(Urun.kod.like(f"{base}%")).count()
        kod = base if sayac == 0 else f"{base}_{sayac}"
    if Urun.query.filter_by(kod=kod).first():
        flash("Bu ürün kodu zaten mevcut.", "warning")
        return redirect(url_for("admin.tanimlar"))
    urun = Urun(kod=kod, ad=ad, birim=birim)
    db.session.add(urun)
    db.session.flush()
    for i in range(1, (paket_sayisi or 1) + 1):
        db.session.add(UrunPaketi(urun_id=urun.id, paket_no=i, paket_adi=f"Paket {i}"))
    db.session.commit()
    flash(f"'{ad}' ürünü {paket_sayisi} paket ile eklendi.", "success")
    return redirect(url_for("admin.tanimlar"))


# ─── 32. ÜRÜN SİL ─────────────────────────────────────────────────────────────

@admin_bp.route("/tanimlar/urun/sil/<int:id>", methods=["POST"])
@admin_required
def urun_sil(id):
    u = Urun.query.get_or_404(id)
    if StokHareketi.query.filter_by(urun_id=id).first():
        flash(f"'{u.ad}' ürününe ait stok hareketi var, silinemez.", "danger")
        return redirect(url_for("admin.tanimlar"))
    if SiparisTalebiKalemi.query.filter_by(urun_id=id).first():
        flash(f"'{u.ad}' ürünü sipariş taleplerinde kullanılmış, silinemez.", "danger")
        return redirect(url_for("admin.tanimlar"))
    try:
        db.session.delete(u)
        db.session.commit()
        flash(f"'{u.ad}' ürünü silindi.", "success")
    except Exception:
        db.session.rollback()
        flash("Ürün silinemedi. Bağlı kayıtlar mevcut.", "danger")
    return redirect(url_for("admin.tanimlar"))


# ─── 33. KATALOG ──────────────────────────────────────────────────────────────

@admin_bp.route("/katalog")
@admin_required
def katalog():
    urunler = KatalogUrun.query.order_by(KatalogUrun.id.desc()).all()
    bekleyen_teklifler = FiyatTeklifi.query.filter_by(durum="beklemede").count()
    katalog_siparisler = (
        SiparisTalebi.query
        .filter(SiparisTalebi.notlar.like("[Katalog:%"))
        .filter(SiparisTalebi.durum.in_(["beklemede", "onaylandi"]))
        .order_by(SiparisTalebi.id.desc())
        .all()
    )
    return render_template(
        "admin/katalog.html",
        urunler=urunler,
        bekleyen_teklifler=bekleyen_teklifler,
        katalog_siparisler=katalog_siparisler
    )


# ─── 34. KATALOG YENİ ─────────────────────────────────────────────────────────

@admin_bp.route("/katalog/yeni", methods=["GET", "POST"])
@admin_required
def katalog_yeni():
    from flask import current_app
    magazalar = Magaza.query.join(Sehir).order_by(Sehir.ad, Magaza.ad).all()
    if request.method == "POST":
        ku = KatalogUrun(
            ad=request.form.get("ad", "").strip(),
            kod=request.form.get("kod", "").strip(),
            kategori=request.form.get("kategori", "").strip(),
            aciklama=request.form.get("aciklama", "").strip(),
            boy=float(request.form.get("boy") or 0) or None,
            en=float(request.form.get("en") or 0) or None,
            derinlik=float(request.form.get("derinlik") or 0) or None,
            agirlik=float(request.form.get("agirlik") or 0) or None,
            fiyat=float(request.form.get("fiyat") or 0) or None,
            fiyat_onaylandi="fiyat_onaylandi" in request.form,
            gorunurluk=request.form.get("gorunurluk", "herkes"),
            aktif=True
        )
        db.session.add(ku)
        db.session.flush()

        if ku.gorunurluk == "secili":
            for mid in request.form.getlist("magaza_ids"):
                db.session.add(KatalogMagazaIzin(
                    katalog_urun_id=ku.id,
                    magaza_id=int(mid),
                    fiyat_gorunsun=f"fiyat_{mid}" in request.form
                ))

        upload_dir = os.path.join(current_app.root_path, "static", "katalog")
        os.makedirs(upload_dir, exist_ok=True)
        for i, f in enumerate(request.files.getlist("resimler")):
            if f and f.filename and izin_verilen(f.filename):
                ext = f.filename.rsplit(".", 1)[1].lower()
                dosya_adi = f"{uuid.uuid4().hex}.{ext}"
                f.save(os.path.join(upload_dir, dosya_adi))
                db.session.add(KatalogResim(urun_id=ku.id, dosya_adi=dosya_adi, sira=i))

        db.session.commit()
        flash("Katalog ürünü eklendi.", "success")
        return redirect(url_for("admin.katalog"))
    return render_template("admin/katalog_form.html", ku=None, magazalar=magazalar)


# ─── 35. KATALOG DÜZENLE ──────────────────────────────────────────────────────

@admin_bp.route("/katalog/<int:id>/duzenle", methods=["GET", "POST"])
@admin_required
def katalog_duzenle(id):
    from flask import current_app
    ku = KatalogUrun.query.get_or_404(id)
    magazalar = Magaza.query.join(Sehir).order_by(Sehir.ad, Magaza.ad).all()
    if request.method == "POST":
        ku.ad = request.form.get("ad", "").strip()
        ku.kod = request.form.get("kod", "").strip()
        ku.kategori = request.form.get("kategori", "").strip()
        ku.aciklama = request.form.get("aciklama", "").strip()
        ku.boy = float(request.form.get("boy") or 0) or None
        ku.en = float(request.form.get("en") or 0) or None
        ku.derinlik = float(request.form.get("derinlik") or 0) or None
        ku.agirlik = float(request.form.get("agirlik") or 0) or None
        ku.fiyat = float(request.form.get("fiyat") or 0) or None
        ku.fiyat_onaylandi = "fiyat_onaylandi" in request.form
        ku.gorunurluk = request.form.get("gorunurluk", "herkes")
        ku.aktif = "aktif" in request.form

        KatalogMagazaIzin.query.filter_by(katalog_urun_id=ku.id).delete()
        if ku.gorunurluk == "secili":
            for mid in request.form.getlist("magaza_ids"):
                db.session.add(KatalogMagazaIzin(
                    katalog_urun_id=ku.id,
                    magaza_id=int(mid),
                    fiyat_gorunsun=f"fiyat_{mid}" in request.form
                ))

        upload_dir = os.path.join(current_app.root_path, "static", "katalog")
        os.makedirs(upload_dir, exist_ok=True)
        mevcut_sira = len(ku.resimler)
        for i, f in enumerate(request.files.getlist("resimler")):
            if f and f.filename and izin_verilen(f.filename):
                ext = f.filename.rsplit(".", 1)[1].lower()
                dosya_adi = f"{uuid.uuid4().hex}.{ext}"
                f.save(os.path.join(upload_dir, dosya_adi))
                db.session.add(KatalogResim(urun_id=ku.id, dosya_adi=dosya_adi, sira=mevcut_sira + i))

        db.session.commit()
        flash("Katalog ürünü güncellendi.", "success")
        return redirect(url_for("admin.katalog"))
    return render_template("admin/katalog_form.html", ku=ku, magazalar=magazalar)


# ─── 36. KATALOG SİL ──────────────────────────────────────────────────────────

@admin_bp.route("/katalog/<int:id>/sil", methods=["POST"])
@admin_required
def katalog_sil(id):
    from flask import current_app
    ku = KatalogUrun.query.get_or_404(id)
    upload_dir = os.path.join(current_app.root_path, "static", "katalog")
    for r in ku.resimler:
        dosya = os.path.join(upload_dir, r.dosya_adi)
        if os.path.exists(dosya):
            os.remove(dosya)
    db.session.delete(ku)
    db.session.commit()
    flash("Katalog ürünü silindi.", "success")
    return redirect(url_for("admin.katalog"))


# ─── 37. KATALOG RESİM SİL ────────────────────────────────────────────────────

@admin_bp.route("/katalog/<int:id>/resim/<int:rid>/sil", methods=["POST"])
@admin_required
def katalog_resim_sil(id, rid):
    from flask import current_app
    r = KatalogResim.query.get_or_404(rid)
    dosya = os.path.join(current_app.root_path, "static", "katalog", r.dosya_adi)
    if os.path.exists(dosya):
        os.remove(dosya)
    db.session.delete(r)
    db.session.commit()
    flash("Resim silindi.", "success")
    return redirect(url_for("admin.katalog_duzenle", id=id))


# ─── 38. KATALOG FİYAT ONAYLA ─────────────────────────────────────────────────

@admin_bp.route("/katalog/<int:id>/fiyat-onayla", methods=["POST"])
@admin_required
def katalog_fiyat_onayla(id):
    ku = KatalogUrun.query.get_or_404(id)
    ku.fiyat_onaylandi = not ku.fiyat_onaylandi
    db.session.commit()
    durum = "onaylandı" if ku.fiyat_onaylandi else "geri alındı"
    flash(f"Fiyat görünürlüğü {durum}.", "success")
    return redirect(url_for("admin.katalog"))


# ─── 39. FİYAT TEKLİFLERİ ─────────────────────────────────────────────────────

@admin_bp.route("/katalog/fiyat-teklifleri")
@admin_required
def fiyat_teklifleri():
    durum = request.args.get("durum", "beklemede")
    q = FiyatTeklifi.query
    if durum != "tumu":
        q = q.filter_by(durum=durum)
    teklifler = q.order_by(FiyatTeklifi.id.desc()).all()
    bekleyen_sayi = FiyatTeklifi.query.filter_by(durum="beklemede").count()
    return render_template(
        "admin/fiyat_teklifleri.html",
        teklifler=teklifler,
        durum_filter=durum,
        bekleyen_sayi=bekleyen_sayi
    )


# ─── 40. FİYAT TEKLİFİ YANITLA ────────────────────────────────────────────────

@admin_bp.route("/katalog/fiyat-teklifleri/<int:id>/yanitla", methods=["POST"])
@admin_required
def fiyat_teklifi_yanitla(id):
    ft = FiyatTeklifi.query.get_or_404(id)
    ft.admin_teklif_fiyati = request.form.get("admin_teklif_fiyati", type=float)
    ft.admin_notu = request.form.get("admin_notu", "").strip()
    ft.durum = "yanitlandi"
    ft.yanitlama_tarihi = datetime.now().strftime("%Y-%m-%d %H:%M")
    db.session.commit()
    flash("Fiyat teklifi yanıtlandı.", "success")
    return redirect(url_for("admin.fiyat_teklifleri"))


# ─── 41. KULLANICILAR ─────────────────────────────────────────────────────────

@admin_bp.route("/kullanicilar")
@admin_required
def kullanicilar():
    users = Kullanici.query.order_by(Kullanici.rol, Kullanici.kullanici_adi).all()
    bekleyenler = Kullanici.query.filter_by(onay_durumu="beklemede").all()
    magazalar = Magaza.query.join(Sehir).order_by(Sehir.ad, Magaza.ad).all()
    return render_template(
        "admin/kullanicilar.html",
        users=users,
        bekleyenler=bekleyenler,
        magazalar=magazalar
    )


# ─── 42. KULLANICI EKLE ───────────────────────────────────────────────────────

@admin_bp.route("/kullanicilar/ekle", methods=["POST"])
@admin_required
def kullanici_ekle():
    kullanici_adi = request.form.get("kullanici_adi", "").strip()
    sifre = request.form.get("sifre", "")
    rol = request.form.get("rol", "magaza")
    magaza_id = request.form.get("magaza_id", type=int)
    ad_soyad = request.form.get("ad_soyad", "").strip()
    email = request.form.get("email", "").strip() or None
    telefon = request.form.get("telefon", "").strip() or None

    if not kullanici_adi or not sifre:
        flash("Kullanıcı adı ve şifre zorunludur.", "warning")
        return redirect(url_for("admin.kullanicilar"))
    if Kullanici.query.filter_by(kullanici_adi=kullanici_adi).first():
        flash("Bu kullanıcı adı zaten mevcut.", "warning")
        return redirect(url_for("admin.kullanicilar"))

    u = Kullanici(
        kullanici_adi=kullanici_adi,
        rol=rol,
        magaza_id=magaza_id if rol == "magaza" else None,
        ad_soyad=ad_soyad,
        email=email,
        telefon=telefon if hasattr(Kullanici, "telefon") else None
    )
    u.set_sifre(sifre)
    db.session.add(u)
    db.session.flush()
    if rol == "magaza":
        db.session.add(KullaniciYetki(kullanici_id=u.id))
    db.session.commit()
    flash(f"Kullanıcı '{kullanici_adi}' oluşturuldu.", "success")
    return redirect(url_for("admin.kullanicilar"))


# ─── 43. KULLANICI ONAYLA ─────────────────────────────────────────────────────

@admin_bp.route("/kullanicilar/<int:id>/onayla", methods=["POST"])
@admin_required
def kullanici_onayla(id):
    u = Kullanici.query.get_or_404(id)
    u.onay_durumu = "onaylandi"
    if u.rol == "magaza" and not u.yetki:
        db.session.add(KullaniciYetki(kullanici_id=u.id))
    db.session.commit()
    flash(f"'{u.kullanici_adi}' hesabı onaylandı.", "success")
    return redirect(url_for("admin.kullanicilar"))


# ─── 44. KULLANICI REDDET ─────────────────────────────────────────────────────

@admin_bp.route("/kullanicilar/<int:id>/reddet", methods=["POST"])
@admin_required
def kullanici_reddet(id):
    u = Kullanici.query.get_or_404(id)
    db.session.delete(u)
    db.session.commit()
    flash("Kayıt talebi reddedildi ve silindi.", "warning")
    return redirect(url_for("admin.kullanicilar"))


# ─── 45. KULLANICI SİL ────────────────────────────────────────────────────────

@admin_bp.route("/kullanicilar/<int:id>/sil", methods=["POST"])
@admin_required
def kullanici_sil(id):
    u = Kullanici.query.get_or_404(id)
    if u.rol == "admin" and Kullanici.query.filter_by(rol="admin").count() == 1:
        flash("Son admin hesabı silinemez.", "danger")
        return redirect(url_for("admin.kullanicilar"))
    if u.id == current_user.id:
        flash("Kendi hesabınızı silemezsiniz.", "danger")
        return redirect(url_for("admin.kullanicilar"))
    SatisHareketi.query.filter_by(kullanici_id=id).delete()
    SiparisTalebi.query.filter_by(kullanici_id=id).delete()
    SshBildirimi.query.filter_by(kullanici_id=id).delete()
    KullaniciYetki.query.filter_by(kullanici_id=id).delete()
    db.session.delete(u)
    db.session.commit()
    flash("Kullanıcı silindi.", "success")
    return redirect(url_for("admin.kullanicilar"))


# ─── 46. KULLANICI ŞİFRE ──────────────────────────────────────────────────────

@admin_bp.route("/kullanicilar/<int:id>/sifre", methods=["POST"])
@admin_required
def kullanici_sifre(id):
    u = Kullanici.query.get_or_404(id)
    yeni_sifre = request.form.get("yeni_sifre", "")
    if yeni_sifre:
        u.set_sifre(yeni_sifre)
        db.session.commit()
        flash("Şifre güncellendi.", "success")
    return redirect(url_for("admin.kullanicilar"))


# ─── 47. KULLANICI YETKİ ──────────────────────────────────────────────────────

@admin_bp.route("/kullanicilar/<int:id>/yetki", methods=["GET", "POST"])
@admin_required
def kullanici_yetki(id):
    u = Kullanici.query.get_or_404(id)
    if not u.yetki:
        u.yetki = KullaniciYetki(kullanici_id=u.id)
        db.session.add(u.yetki)
        db.session.commit()
    if request.method == "POST":
        y = u.yetki
        y.stok = "stok" in request.form
        y.satis = "satis" in request.form
        y.sevklerim = "sevklerim" in request.form
        y.talepler = "talepler" in request.form
        y.ssh = "ssh" in request.form
        y.katalog = "katalog" in request.form
        y.katalog_fiyat = "katalog_fiyat" in request.form
        db.session.commit()
        flash(f"'{u.kullanici_adi}' yetkileri güncellendi.", "success")
        return redirect(url_for("admin.kullanicilar"))
    return render_template("admin/kullanici_yetki.html", u=u)


# ─── 48. RAPORLAR ─────────────────────────────────────────────────────────────

@admin_bp.route("/raporlar")
@admin_required
def raporlar():
    from sqlalchemy import func as sqlfunc
    magazalar = Magaza.query.join(Sehir).order_by(Sehir.ad, Magaza.ad).all()
    sehirler = Sehir.query.order_by(Sehir.ad).all()

    # Sevk satırları (son 20)
    sevkler = Sevk.query.order_by(Sevk.id.desc()).limit(20).all()
    sevk_satirlar = []
    for s in sevkler:
        ara = (s.nakliye_ucreti or 0) + (s.iscilik or 0) + sum(g.tutar for g in s.giderler)
        kdv = ara * (s.kdv_oran or 0) / 100
        magaza_adi = (
            f"{s.magaza.ad}/{s.magaza.sehir.ad}"
            if s.magaza
            else (s.alici_adi or "Serbest")
        )
        sevk_satirlar.append({
            "id": s.id,
            "tarih": s.tarih,
            "magaza": magaza_adi,
            "urunler": [f"{k.urun.ad} \u00d7{int(k.miktar)}" for k in s.kalemler],
            "toplam": int(ara + kdv)
        })

    # Stok satırları
    urunler = Urun.query.order_by(Urun.ad).all()
    stok_satirlar = []
    for u in urunler:
        g = db.session.query(
            sqlfunc.coalesce(sqlfunc.sum(StokHareketi.miktar), 0)
        ).filter(
            StokHareketi.urun_id == u.id,
            StokHareketi.hareket_turu.in_(GIRIS_TURLERI)
        ).scalar() or 0
        c = db.session.query(
            sqlfunc.coalesce(sqlfunc.sum(sqlfunc.abs(StokHareketi.miktar)), 0)
        ).filter(
            StokHareketi.urun_id == u.id,
            StokHareketi.hareket_turu.in_(CIKIS_TURLERI)
        ).scalar() or 0
        stok_satirlar.append({"urun": u.ad, "adet": int(g - c)})

    # Mağaza maliyet satırları
    mag_maliyet = []
    for m in magazalar:
        toplam = sum(
            (s.nakliye_ucreti or 0) + (s.iscilik or 0) + sum(gg.tutar for gg in s.giderler)
            for s in m.sevkler
        )
        if toplam > 0:
            mag_maliyet.append({"magaza": f"{m.ad}/{m.sehir.ad}", "toplam": int(toplam)})
    mag_maliyet.sort(key=lambda x: x["toplam"], reverse=True)

    # SSH satırları (son 20)
    ssh_liste = SshBildirimi.query.order_by(SshBildirimi.id.desc()).limit(20).all()
    ssh_satirlar = [
        {"magaza": s.magaza.ad, "urun": s.urun.ad, "durum": s.durum}
        for s in ssh_liste
    ]

    rapor_ozet = {
        "sevk": {"satirlar": sevk_satirlar},
        "maliyet": {"satirlar": mag_maliyet},
        "stok": {"satirlar": stok_satirlar},
        "ssh": {"satirlar": ssh_satirlar},
    }
    return render_template(
        "admin/raporlar.html",
        magazalar=magazalar,
        sehirler=sehirler,
        rapor_ozet=rapor_ozet
    )


# ─── 49. RAPOR EXCEL ──────────────────────────────────────────────────────────

@admin_bp.route("/raporlar/excel/<string:tur>")
@admin_required
def rapor_excel(tur):
    from utils.excel_export import export_sevk_ozet, export_magaza_maliyet, export_stok, export_ssh

    buf = io.BytesIO()
    if tur == "sevk":
        sevkler = Sevk.query.order_by(Sevk.id.desc()).all()
        export_sevk_ozet(sevkler, buf)
        fname = "sevk_ozeti.xlsx"
    elif tur == "maliyet":
        magazalar = Magaza.query.all()
        export_magaza_maliyet(magazalar, buf)
        fname = "magaza_maliyet.xlsx"
    elif tur == "stok":
        ozet = _stok_ozet()
        rezerve = _rezerve_ozet()
        for item in ozet:
            item["rezerve"] = rezerve.get(item["urun"].id, 0)
            item["kullanilabilir"] = item["bakiye"] - item["rezerve"]
        export_stok(ozet, buf)
        fname = "stok_durumu.xlsx"
    elif tur == "ssh":
        bildirimleri = SshBildirimi.query.all()
        export_ssh(bildirimleri, buf)
        fname = "ssh_bildirimleri.xlsx"
    else:
        flash("Geçersiz rapor türü.", "danger")
        return redirect(url_for("admin.raporlar"))

    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
