from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required, current_user
from functools import wraps
from models import (db, Sehir, Magaza, Urun, UrunPaketi, Siparis, UretimPaketGirisi,
                    StokHareketi, Sevk, SevkKalemi, GenelGider,
                    SiparisTalebi, SiparisTalebiKalemi, SshBildirimi, Kullanici, KullaniciYetki,
                    KatalogUrun, KatalogResim, KatalogMagazaIzin, SatisHareketi, FiyatTeklifi)
from datetime import datetime
import os, uuid
from werkzeug.utils import secure_filename

IZINLI_UZANTILAR = {"jpg", "jpeg", "png", "webp", "gif"}

def izin_verilen(dosya_adi):
    return "." in dosya_adi and dosya_adi.rsplit(".", 1)[1].lower() in IZINLI_UZANTILAR

admin_bp = Blueprint("admin", __name__)


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash("Bu sayfaya erisim yetkiniz yok.", "danger")
            return redirect(url_for("magaza.dashboard"))
        return f(*args, **kwargs)
    return decorated


# ─── DASHBOARD ────────────────────────────────────────────────────────────────

@admin_bp.route("/")
@admin_required
def dashboard():
    bekleyen_talepler = SiparisTalebi.query.filter_by(durum="beklemede").count()
    acik_ssh = SshBildirimi.query.filter(SshBildirimi.durum != "teslim_edildi").count()
    aktif_siparisler = Siparis.query.filter(Siparis.durum != "tamamlandi").all()
    for s in aktif_siparisler:
        s._sevk_edilebilir = s.sevk_edilebilir_takim()
        s._eksikler = s.eksik_paketler()
    son_sevkler = Sevk.query.order_by(Sevk.id.desc()).limit(5).all()
    stok_ozet = _stok_ozet()
    return render_template("admin/dashboard.html",
                           bekleyen_talepler=bekleyen_talepler,
                           acik_ssh=acik_ssh,
                           aktif_siparisler=aktif_siparisler,
                           son_sevkler=son_sevkler,
                           stok_ozet=stok_ozet)


def _stok_ozet():
    GIRIS = ["uretim_giris", "duzeltme_giris", "iade"]
    CIKIS = ["sevk_cikis", "duzeltme_cikis", "fire"]
    urunler = Urun.query.all()
    ozet = []
    for u in urunler:
        giris = sum(h.miktar for h in u.stok_hareketleri if h.hareket_turu in GIRIS)
        cikis = sum(abs(h.miktar) for h in u.stok_hareketleri if h.hareket_turu in CIKIS)
        bakiye = giris - cikis
        ozet.append({"urun": u, "bakiye": bakiye})
    return ozet


# ─── TANIMLAR ─────────────────────────────────────────────────────────────────

@admin_bp.route("/tanimlar")
@admin_required
def tanimlar():
    sehirler = Sehir.query.order_by(Sehir.ad).all()
    magazalar = Magaza.query.join(Sehir).order_by(Sehir.ad, Magaza.ad).all()
    urunler = Urun.query.order_by(Urun.ad).all()
    return render_template("admin/tanimlar.html", sehirler=sehirler, magazalar=magazalar, urunler=urunler)


@admin_bp.route("/tanimlar/sehir/ekle", methods=["POST"])
@admin_required
def sehir_ekle():
    ad = request.form.get("ad", "").strip()
    if ad:
        if not Sehir.query.filter_by(ad=ad).first():
            db.session.add(Sehir(ad=ad))
            db.session.commit()
            flash(f"'{ad}' sehri eklendi.", "success")
        else:
            flash("Bu sehir zaten mevcut.", "warning")
    return redirect(url_for("admin.tanimlar"))


@admin_bp.route("/tanimlar/sehir/sil/<int:id>", methods=["POST"])
@admin_required
def sehir_sil(id):
    s = Sehir.query.get_or_404(id)
    db.session.delete(s)
    db.session.commit()
    flash("Sehir silindi.", "success")
    return redirect(url_for("admin.tanimlar"))


@admin_bp.route("/tanimlar/magaza/ekle", methods=["POST"])
@admin_required
def magaza_ekle():
    ad = request.form.get("ad", "").strip()
    sehir_ad = request.form.get("sehir_ad", "").strip().upper()
    adres = request.form.get("adres", "").strip()
    telefon = request.form.get("telefon", "").strip()
    if ad and sehir_ad:
        sehir = Sehir.query.filter(db.func.upper(Sehir.ad) == sehir_ad).first()
        if not sehir:
            sehir = Sehir(ad=sehir_ad)
            db.session.add(sehir)
            db.session.flush()
        db.session.add(Magaza(ad=ad, sehir_id=sehir.id, adres=adres, telefon=telefon))
        db.session.commit()
        flash(f"'{ad}' magazasi eklendi.", "success")
    return redirect(url_for("admin.tanimlar"))


@admin_bp.route("/tanimlar/magaza/sil/<int:id>", methods=["POST"])
@admin_required
def magaza_sil(id):
    m = Magaza.query.get_or_404(id)
    db.session.delete(m)
    db.session.commit()
    flash("Magaza silindi.", "success")
    return redirect(url_for("admin.tanimlar"))


@admin_bp.route("/tanimlar/urun/ekle", methods=["POST"])
@admin_required
def urun_ekle():
    kod = request.form.get("kod", "").strip()
    ad = request.form.get("ad", "").strip()
    birim = request.form.get("birim", "takim").strip()
    paket_sayisi = request.form.get("paket_sayisi", type=int, default=1)
    if kod and ad:
        if Urun.query.filter_by(kod=kod).first():
            flash("Bu urun kodu zaten mevcut.", "warning")
            return redirect(url_for("admin.tanimlar"))
        if Urun.query.filter(db.func.upper(Urun.ad) == ad.upper()).first():
            flash("Bu urun adi zaten mevcut.", "warning")
            return redirect(url_for("admin.tanimlar"))
        urun = Urun(kod=kod, ad=ad, birim=birim)
        db.session.add(urun)
        db.session.flush()
        for i in range(1, paket_sayisi + 1):
            db.session.add(UrunPaketi(urun_id=urun.id, paket_no=i, paket_adi=f"Paket {i}"))
        db.session.commit()
        flash(f"'{ad}' urunu {paket_sayisi} paket ile eklendi.", "success")
    return redirect(url_for("admin.tanimlar"))


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
        flash("Paket adlari guncellendi.", "success")
        return redirect(url_for("admin.tanimlar"))
    return render_template("admin/paket_duzenle.html", urun=urun)


@admin_bp.route("/tanimlar/urun/sil/<int:id>", methods=["POST"])
@admin_required
def urun_sil(id):
    u = Urun.query.get_or_404(id)
    stok_var = StokHareketi.query.filter_by(urun_id=id).first()
    sevk_var = SevkKalemi.query.filter_by(urun_id=id).first()
    talep_var = SiparisTalebiKalemi.query.filter_by(urun_id=id).first()
    if stok_var or sevk_var or talep_var:
        flash(f"'{u.ad}' silinemedi. Bu urune ait stok veya sevkiyat kaydi mevcut. Once ilgili kayitlari silin.", "danger")
        return redirect(url_for("admin.tanimlar"))
    db.session.delete(u)
    db.session.commit()
    flash("Urun silindi.", "success")
    return redirect(url_for("admin.tanimlar"))


# ─── ÜRETİM ──────────────────────────────────────────────────────────────────

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
    return render_template("admin/uretim.html", siparisler=siparisler, urunler=urunler,
                           now=datetime.now(), durum_filter=durum_filter,
                           aktif_sayi=aktif_sayi, tamamlandi_sayi=tamamlandi_sayi)


@admin_bp.route("/uretim/yeni", methods=["POST"])
@admin_required
def uretim_yeni():
    urun_ad = request.form.get("urun_ad", "").strip()
    paket_sayisi = request.form.get("paket_sayisi", type=int, default=1)
    siparis_adeti = request.form.get("siparis_adeti", type=float)
    notlar = request.form.get("notlar", "").strip()
    tarih = request.form.get("tarih", datetime.now().strftime("%Y-%m-%d"))
    if not urun_ad or not siparis_adeti:
        flash("Urun adi ve siparis adeti zorunludur.", "warning")
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
    flash("Siparis olusturuldu. Paket miktarlarini girin.", "success")
    return redirect(url_for("admin.paket_gir", id=s.id))


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
                db.session.add(UretimPaketGirisi(siparis_id=id, paket_id=p.id, uretilen_miktar=miktar))
        sevk_edilebilir = siparis.sevk_edilebilir_takim()
        if sevk_edilebilir >= siparis.siparis_adeti:
            siparis.durum = "hazir"
        else:
            siparis.durum = "uretimde"
        db.session.commit()
        _uretim_stok_gir(siparis)
        flash("Paket miktarlari kaydedildi.", "success")
        return redirect(url_for("admin.uretim"))
    mevcut_girişler = {g.paket_id: g.uretilen_miktar for g in siparis.uretim_girisleri}
    return render_template("admin/paket_gir.html", siparis=siparis, mevcut=mevcut_girişler)


@admin_bp.route("/uretim/<int:id>/tamamla", methods=["POST"])
@admin_required
def uretim_tamamla(id):
    siparis = Siparis.query.get_or_404(id)
    siparis.durum = "tamamlandi"
    for g in siparis.uretim_girisleri:
        g.uretilen_miktar = 0
    db.session.commit()
    flash(f"'{siparis.urun.ad}' uretim siparisi tamamlandi olarak isaretlendi.", "success")
    return redirect(url_for("admin.uretim"))


@admin_bp.route("/uretim/<int:id>/tekrar-ac", methods=["POST"])
@admin_required
def uretim_tekrar_ac(id):
    siparis = Siparis.query.get_or_404(id)
    siparis.durum = "uretimde"
    db.session.commit()
    flash("Siparis tekrar aktife alindi.", "success")
    return redirect(url_for("admin.uretim", durum="aktif"))


@admin_bp.route("/uretim/<int:id>/sil", methods=["POST"])
@admin_required
def uretim_sil(id):
    siparis = Siparis.query.get_or_404(id)
    StokHareketi.query.filter_by(kaynak="uretim", referans_id=id).delete()
    UretimPaketGirisi.query.filter_by(siparis_id=id).delete()
    db.session.delete(siparis)
    db.session.commit()
    flash("Uretim siparisi silindi.", "success")
    return redirect(url_for("admin.uretim"))


def _uretim_stok_gir(siparis):
    sevk_edilebilir = siparis.sevk_edilebilir_takim()
    mevcut = StokHareketi.query.filter_by(
        kaynak="uretim", referans_id=siparis.id, hareket_turu="uretim_giris"
    ).first()
    if mevcut:
        mevcut.miktar = sevk_edilebilir
        mevcut.tarih = datetime.now().strftime("%Y-%m-%d")
    else:
        if sevk_edilebilir > 0:
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


# ─── STOK ─────────────────────────────────────────────────────────────────────

@admin_bp.route("/stok")
@admin_required
def stok():
    ozet = _stok_ozet()
    rezerve = _rezerve_ozet()
    for item in ozet:
        uid = item["urun"].id
        item["rezerve"] = rezerve.get(uid, 0)
        item["kullanilabilir"] = item["bakiye"] - item["rezerve"]
    hareketler = StokHareketi.query.order_by(StokHareketi.id.desc()).limit(100).all()
    urunler = Urun.query.order_by(Urun.ad).all()
    return render_template("admin/stok.html", ozet=ozet, hareketler=hareketler, urunler=urunler)


@admin_bp.route("/stok/manuel-giris", methods=["POST"])
@admin_required
def stok_manuel_giris():
    urun_id = request.form.get("urun_id", type=int)
    miktar = request.form.get("miktar", type=int)
    hareket_turu = request.form.get("hareket_turu", "duzeltme_giris")
    aciklama = request.form.get("aciklama", "").strip()
    if urun_id and miktar and miktar != 0:
        h = StokHareketi(
            tarih=datetime.now(),
            urun_id=urun_id,
            hareket_turu=hareket_turu,
            miktar=miktar,
            kaynak=f"Manuel Admin: {aciklama}" if aciklama else "Manuel Admin",
            depo="ana_depo"
        )
        db.session.add(h)
        db.session.commit()
        flash(f"Manuel stok hareketi eklendi: {miktar:+} adet.", "success")
    else:
        flash("Urun ve miktar zorunlu.", "warning")
    return redirect(url_for("admin.stok"))


@admin_bp.route("/stok/hareket/<int:id>/sil", methods=["POST"])
@admin_required
def stok_hareket_sil(id):
    h = StokHareketi.query.get_or_404(id)
    db.session.delete(h)
    db.session.commit()
    flash("Hareket kaydi silindi.", "success")
    return redirect(url_for("admin.stok"))


@admin_bp.route("/stok/hareket/<int:id>/duzenle", methods=["POST"])
@admin_required
def stok_hareket_duzenle(id):
    h = StokHareketi.query.get_or_404(id)
    yeni_miktar = request.form.get("miktar", type=int)
    aciklama = request.form.get("aciklama", "").strip()
    if yeni_miktar is not None:
        h.miktar = yeni_miktar
    if aciklama:
        h.kaynak = aciklama
    db.session.commit()
    flash("Hareket kaydi guncellendi.", "success")
    return redirect(url_for("admin.stok"))


def _urun_bul_veya_olustur(ad):
    ad_temiz = ad.strip().upper()
    urun = Urun.query.filter(db.func.upper(Urun.ad) == ad_temiz).first()
    if not urun:
        import re
        kod = re.sub(r"[^A-Z0-9]", "_", ad_temiz)[:20]
        sayac = Urun.query.filter(Urun.kod.like(f"{kod}%")).count()
        if sayac:
            kod = f"{kod}_{sayac}"
        urun = Urun(kod=kod, ad=ad.strip(), birim="takim")
        db.session.add(urun)
        db.session.flush()
    return urun


def _rezerve_ozet():
    sonuc = {}
    aktif_durumlar = ("beklemede", "onaylandi")
    kalemler = (
        db.session.query(SiparisTalebiKalemi)
        .join(SiparisTalebi, SiparisTalebiKalemi.talep_id == SiparisTalebi.id)
        .filter(SiparisTalebi.durum.in_(aktif_durumlar))
        .all()
    )
    for k in kalemler:
        sonuc[k.urun_id] = sonuc.get(k.urun_id, 0) + k.miktar
    return sonuc


# ─── SEVKİYAT ─────────────────────────────────────────────────────────────────

@admin_bp.route("/talepler/<int:talep_id>/sevk-hizli", methods=["POST"])
@admin_required
def sevk_hizli(talep_id):
    talep = SiparisTalebi.query.get_or_404(talep_id)
    tarih = datetime.now().strftime("%Y-%m-%d")
    nakliye = request.form.get("nakliye_ucreti", type=float, default=0)
    iscilik = request.form.get("iscilik", type=float, default=0)
    kdv_oran = request.form.get("kdv_oran", type=int, default=0)
    notlar = request.form.get("notlar", "").strip()
    nakliye_goster = bool(request.form.get("nakliye_goster"))
    sevk = Sevk(tarih=tarih, magaza_id=talep.magaza_id, talep_id=talep_id,
                nakliye_ucreti=nakliye, iscilik=iscilik, kdv_oran=kdv_oran,
                notlar=notlar, nakliye_goster=nakliye_goster)
    db.session.add(sevk)
    db.session.flush()
    from sqlalchemy import func as sqlfunc
    for k in talep.kalemler:
        db.session.add(SevkKalemi(sevk_id=sevk.id, urun_id=k.urun_id, miktar=k.miktar))
        db.session.add(StokHareketi(
            tarih=tarih, urun_id=k.urun_id, hareket_turu="sevk_cikis",
            miktar=k.miktar, kaynak="sevk", referans_id=sevk.id, depo="ana_depo"
        ))
        giris = db.session.query(sqlfunc.coalesce(sqlfunc.sum(StokHareketi.miktar), 0))\
            .filter(StokHareketi.urun_id == k.urun_id,
                    StokHareketi.hareket_turu.in_(["uretim_giris","duzeltme_giris","iade"])).scalar() or 0
        cikis = db.session.query(sqlfunc.coalesce(sqlfunc.sum(sqlfunc.abs(StokHareketi.miktar)), 0))\
            .filter(StokHareketi.urun_id == k.urun_id,
                    StokHareketi.hareket_turu.in_(["sevk_cikis","duzeltme_cikis","fire"])).scalar() or 0
        if int(giris) - int(cikis) <= 0:
            for sp in Siparis.query.filter_by(urun_id=k.urun_id).filter(Siparis.durum.in_(["hazir","uretimde"])).all():
                sp.durum = "tamamlandi"
                for g in sp.uretim_girisleri:
                    g.uretilen_miktar = 0
    talep.durum = "sevk_edildi"
    db.session.commit()
    flash(f"Sevk olusturuldu (#{sevk.id}).", "success")
    return redirect(url_for("admin.talepler"))


@admin_bp.route("/talepler/<int:talep_id>/iptal", methods=["POST"])
@admin_required
def talep_iptal(talep_id):
    talep = SiparisTalebi.query.get_or_404(talep_id)
    sebep = request.form.get("sebep", "").strip()
    talep.durum = "iptal"
    talep.iptal_sebebi = sebep
    db.session.commit()
    flash(f"Talep #{talep_id} iptal edildi.", "warning")
    return redirect(url_for("admin.talepler"))


@admin_bp.route("/sevk/<int:sevk_id>/duzenle", methods=["POST"])
@admin_required
def sevk_duzenle(sevk_id):
    sevk = Sevk.query.get_or_404(sevk_id)
    sevk.nakliye_ucreti = request.form.get("nakliye_ucreti", type=float, default=0)
    sevk.iscilik = request.form.get("iscilik", type=float, default=0)
    sevk.kdv_oran = request.form.get("kdv_oran", type=int, default=0)
    sevk.notlar = request.form.get("notlar", "").strip()
    sevk.nakliye_goster = bool(request.form.get("nakliye_goster"))
    urun_ids = request.form.getlist("urun_id[]")
    miktarlar = request.form.getlist("miktar[]")
    StokHareketi.query.filter_by(referans_id=sevk.id, hareket_turu="sevk_cikis").delete()
    SevkKalemi.query.filter_by(sevk_id=sevk.id).delete()
    db.session.flush()
    tarih = sevk.tarih
    for uid, mkt in zip(urun_ids, miktarlar):
        try:
            mkt_f = float(mkt or 0)
        except ValueError:
            continue
        if not uid or mkt_f <= 0:
            continue
        urun = Urun.query.get(int(uid))
        if not urun:
            continue
        db.session.add(SevkKalemi(sevk_id=sevk.id, urun_id=urun.id, miktar=mkt_f))
        db.session.add(StokHareketi(
            tarih=tarih, urun_id=urun.id, hareket_turu="sevk_cikis",
            miktar=mkt_f, kaynak="sevk", referans_id=sevk.id, depo="ana_depo"
        ))
    db.session.commit()
    flash(f"Sevk #{sevk_id} guncellendi.", "success")
    return redirect(url_for("admin.sevk_listesi"))


@admin_bp.route("/sevk")
@admin_required
def sevk_listesi():
    sevkler = Sevk.query.order_by(Sevk.id.desc()).all()
    magazalar = Magaza.query.join(Sehir).order_by(Sehir.ad, Magaza.ad).all()
    urunler = Urun.query.order_by(Urun.ad).all()
    talep_id = request.args.get("talep_id", type=int)
    talep = SiparisTalebi.query.get(talep_id) if talep_id else None
    return render_template("admin/sevk.html", sevkler=sevkler,
                           magazalar=magazalar, urunler=urunler,
                           now=datetime.now(), talep=talep)


@admin_bp.route("/sevk/yeni", methods=["POST"])
@admin_required
def sevk_yeni():
    magaza_id = request.form.get("magaza_id", type=int)
    alici_turu = request.form.get("alici_turu", "magaza")
    alici_adi = request.form.get("alici_adi", "").strip()
    tarih = request.form.get("tarih", datetime.now().strftime("%Y-%m-%d"))
    nakliye = request.form.get("nakliye_ucreti", type=float, default=0)
    iscilik = request.form.get("iscilik", type=float, default=0)
    kdv_oran = request.form.get("kdv_oran", type=int, default=0)
    notlar = request.form.get("notlar", "").strip()
    talep_id = request.form.get("talep_id", type=int)
    if alici_turu == "serbest":
        magaza_id = None
        if not alici_adi:
            flash("Serbest alici adi giriniz.", "warning")
            return redirect(url_for("admin.sevk_listesi"))
    urun_ids = request.form.getlist("urun_id[]")
    urun_adlar = request.form.getlist("urun_ad[]")
    miktarlar = request.form.getlist("miktar[]")
    gider_turler = request.form.getlist("gider_tur[]")
    gider_tutarlar = request.form.getlist("gider_tutar[]")
    gider_aciklamalar = request.form.getlist("gider_aciklama[]")
    kalemler = []
    for i, mkt in enumerate(miktarlar):
        try:
            mkt_f = float(mkt or 0)
        except ValueError:
            continue
        if mkt_f <= 0:
            continue
        urun = None
        if i < len(urun_ids) and urun_ids[i]:
            urun = Urun.query.get(int(urun_ids[i]))
        if not urun and i < len(urun_adlar) and urun_adlar[i].strip():
            urun = _urun_bul_veya_olustur(urun_adlar[i])
        if urun:
            kalemler.append((urun, mkt_f))
    if (alici_turu == "magaza" and not magaza_id) or not kalemler:
        flash("Magaza ve en az bir urun girmelisiniz.", "warning")
        return redirect(url_for("admin.sevk_listesi"))
    sevk = Sevk(tarih=tarih, magaza_id=magaza_id, nakliye_ucreti=nakliye,
                iscilik=iscilik, kdv_oran=kdv_oran, notlar=notlar, talep_id=talep_id,
                alici_turu=alici_turu, alici_adi=alici_adi if alici_turu == "serbest" else None)
    db.session.add(sevk)
    db.session.flush()
    for urun, mkt in kalemler:
        db.session.add(SevkKalemi(sevk_id=sevk.id, urun_id=urun.id, miktar=mkt))
        db.session.add(StokHareketi(
            tarih=tarih, urun_id=urun.id, hareket_turu="sevk_cikis",
            miktar=mkt, kaynak="sevk", referans_id=sevk.id, depo="ana_depo"
        ))
    for tur, tutar, aciklama in zip(gider_turler, gider_tutarlar, gider_aciklamalar):
        tutar = float(tutar or 0)
        if tur and tutar > 0:
            db.session.add(GenelGider(sevk_id=sevk.id, gider_turu=tur, tutar=tutar, aciklama=aciklama))
    if talep_id:
        t = SiparisTalebi.query.get(talep_id)
        if t:
            t.durum = "sevk_edildi"
    for urun_obj, mkt in kalemler:
        from sqlalchemy import func as sqlfunc
        giris = db.session.query(sqlfunc.coalesce(sqlfunc.sum(StokHareketi.miktar), 0))\
            .filter(StokHareketi.urun_id == urun_obj.id,
                    StokHareketi.hareket_turu.in_(["uretim_giris","duzeltme_giris","iade"])).scalar() or 0
        cikis = db.session.query(sqlfunc.coalesce(sqlfunc.sum(sqlfunc.abs(StokHareketi.miktar)), 0))\
            .filter(StokHareketi.urun_id == urun_obj.id,
                    StokHareketi.hareket_turu.in_(["sevk_cikis","duzeltme_cikis","fire"])).scalar() or 0
        if int(giris) - int(cikis) <= 0:
            aktif = Siparis.query.filter_by(urun_id=urun_obj.id)\
                .filter(Siparis.durum.in_(["hazir","uretimde"])).all()
            for sp in aktif:
                sp.durum = "tamamlandi"
                for g in sp.uretim_girisleri:
                    g.uretilen_miktar = 0
    db.session.commit()
    flash("Sevk kaydedildi.", "success")
    return redirect(url_for("admin.sevk_listesi"))


@admin_bp.route("/sevk/<int:id>/sil", methods=["POST"])
@admin_required
def sevk_sil(id):
    sevk = Sevk.query.get_or_404(id)
    StokHareketi.query.filter_by(kaynak="sevk", referans_id=id).delete()
    db.session.delete(sevk)
    db.session.commit()
    flash("Sevk silindi.", "success")
    return redirect(url_for("admin.sevk_listesi"))


# ─── SİPARİŞ TALEPLERİ ───────────────────────────────────────────────────────

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
    kullanilabilir = {uid: stok.get(uid, 0) - rezerve.get(uid, 0) for uid in set(list(stok.keys()) + list(rezerve.keys()))}
    return render_template("admin/talepler.html", talepler=talepler_listesi,
                           durum_filter=durum_filter, stok=stok,
                           rezerve=rezerve, kullanilabilir=kullanilabilir,
                           beklemede_sayi=SiparisTalebi.query.filter_by(durum="beklemede").count())


@admin_bp.route("/talepler/<int:id>/onayla", methods=["POST"])
@admin_required
def talep_onayla(id):
    t = SiparisTalebi.query.get_or_404(id)
    t.durum = "onaylandi"
    db.session.commit()
    flash("Talep onaylandi.", "success")
    return redirect(url_for("admin.talepler"))


@admin_bp.route("/talepler/<int:id>/reddet", methods=["POST"])
@admin_required
def talep_reddet(id):
    t = SiparisTalebi.query.get_or_404(id)
    t.durum = "iptal"
    db.session.commit()
    flash("Talep reddedildi.", "warning")
    return redirect(url_for("admin.talepler"))


# ─── SSH ──────────────────────────────────────────────────────────────────────

@admin_bp.route("/ssh")
@admin_required
def ssh_listesi():
    durum_filter = request.args.get("durum", "")
    q = SshBildirimi.query
    if durum_filter:
        q = q.filter_by(durum=durum_filter)
    bildirimleri = q.order_by(SshBildirimi.id.desc()).all()
    return render_template("admin/ssh.html", bildirimleri=bildirimleri, durum_filter=durum_filter)


@admin_bp.route("/ssh/<int:id>/durum", methods=["POST"])
@admin_required
def ssh_durum(id):
    b = SshBildirimi.query.get_or_404(id)
    b.durum = request.form.get("durum", b.durum)
    b.admin_notu = request.form.get("admin_notu", b.admin_notu)
    db.session.commit()
    flash("SSH durumu guncellendi.", "success")
    return redirect(url_for("admin.ssh_listesi"))


# ─── RAPORLAR ────────────────────────────────────────────────────────────────

@admin_bp.route("/raporlar")
@admin_required
def raporlar():
    from sqlalchemy import func as sqlfunc
    magazalar = Magaza.query.join(Sehir).order_by(Sehir.ad, Magaza.ad).all()
    sehirler = Sehir.query.order_by(Sehir.ad).all()
    sevkler = Sevk.query.order_by(Sevk.id.desc()).limit(20).all()
    sevk_satirlar = []
    for s in sevkler:
        ara = (s.nakliye_ucreti or 0) + (s.iscilik or 0) + sum(g.tutar for g in s.giderler)
        kdv = ara * (s.kdv_oran or 0) / 100
        sevk_satirlar.append({
            "id": s.id, "tarih": s.tarih,
            "magaza": f"{s.magaza.ad}/{s.magaza.sehir.ad}",
            "urunler": [f"{k.urun.ad} x{int(k.miktar)}" for k in s.kalemler],
            "toplam": int(ara + kdv)
        })
    GIRIS = ["uretim_giris", "duzeltme_giris", "iade"]
    CIKIS = ["sevk_cikis", "duzeltme_cikis", "fire"]
    urunler = Urun.query.order_by(Urun.ad).all()
    stok_satirlar = []
    for u in urunler:
        g = db.session.query(sqlfunc.coalesce(sqlfunc.sum(StokHareketi.miktar), 0))\
            .filter(StokHareketi.urun_id == u.id, StokHareketi.hareket_turu.in_(GIRIS)).scalar() or 0
        c = db.session.query(sqlfunc.coalesce(sqlfunc.sum(sqlfunc.abs(StokHareketi.miktar)), 0))\
            .filter(StokHareketi.urun_id == u.id, StokHareketi.hareket_turu.in_(CIKIS)).scalar() or 0
        stok_satirlar.append({"urun": u.ad, "adet": int(g - c)})
    mag_maliyet = []
    for m in magazalar:
        toplam = sum(
            (s.nakliye_ucreti or 0) + (s.iscilik or 0) + sum(g.tutar for g in s.giderler)
            for s in m.sevkler
        )
        if toplam > 0:
            mag_maliyet.append({"magaza": f"{m.ad}/{m.sehir.ad}", "toplam": int(toplam)})
    mag_maliyet.sort(key=lambda x: x["toplam"], reverse=True)
    ssh_liste = SshBildirimi.query.order_by(SshBildirimi.id.desc()).limit(20).all()
    ssh_satirlar = [{"magaza": s.magaza.ad, "urun": s.urun.ad, "durum": s.durum} for s in ssh_liste]
    rapor_ozet = {
        "sevk": {"satirlar": sevk_satirlar},
        "maliyet": {"satirlar": mag_maliyet},
        "stok": {"satirlar": stok_satirlar},
        "ssh": {"satirlar": ssh_satirlar},
    }
    return render_template("admin/raporlar.html", magazalar=magazalar, sehirler=sehirler,
                           rapor_ozet=rapor_ozet)


@admin_bp.route("/raporlar/excel/<string:tur>")
@admin_required
def rapor_excel(tur):
    from utils.excel_export import (export_sevk_ozet, export_magaza_maliyet,
                                    export_stok, export_ssh)
    import io
    from flask import send_file
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
        export_stok(ozet, buf)
        fname = "stok_durumu.xlsx"
    elif tur == "ssh":
        bildirimleri = SshBildirimi.query.all()
        export_ssh(bildirimleri, buf)
        fname = "ssh_bildirimleri.xlsx"
    else:
        flash("Gecersiz rapor turu.", "danger")
        return redirect(url_for("admin.raporlar"))
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ─── MAĞAZA STOK ─────────────────────────────────────────────────────────────

@admin_bp.route("/magaza-stok")
@admin_required
def magaza_stok():
    from magaza import magaza_stok_ozet
    magazalar = Magaza.query.join(Sehir).order_by(Sehir.ad, Magaza.ad).all()
    sehirler = Sehir.query.order_by(Sehir.ad).all()
    secili_magaza_id = request.args.get("magaza_id", type=int)
    secili_sehir_id = request.args.get("sehir_id", type=int)
    magaza_ozetleri = []
    for m in magazalar:
        if secili_sehir_id and m.sehir_id != secili_sehir_id:
            continue
        if secili_magaza_id and m.id != secili_magaza_id:
            continue
        stok = magaza_stok_ozet(m.id)
        magaza_ozetleri.append({"magaza": m, "stok": stok})
    return render_template("admin/magaza_stok.html",
                           magaza_ozetleri=magaza_ozetleri,
                           magazalar=magazalar,
                           sehirler=sehirler,
                           secili_magaza_id=secili_magaza_id,
                           secili_sehir_id=secili_sehir_id)


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
    return render_template("admin/magaza_satis.html",
                           satirlar=satirlar, magazalar=magazalar,
                           sehirler=sehirler,
                           sehir_id=sehir_id, magaza_id=magaza_id,
                           tarih_bas=tarih_bas, tarih_bit=tarih_bit)


# ─── KULLANICILAR ────────────────────────────────────────────────────────────

@admin_bp.route("/kullanicilar")
@admin_required
def kullanicilar():
    users = Kullanici.query.order_by(Kullanici.rol, Kullanici.kullanici_adi).all()
    bekleyenler = Kullanici.query.filter_by(onay_durumu="beklemede").all()
    magazalar = Magaza.query.join(Sehir).order_by(Sehir.ad, Magaza.ad).all()
    return render_template("admin/kullanicilar.html", users=users, magazalar=magazalar, bekleyenler=bekleyenler)


@admin_bp.route("/kullanicilar/<int:id>/onayla", methods=["POST"])
@admin_required
def kullanici_onayla(id):
    u = Kullanici.query.get_or_404(id)
    u.onay_durumu = "onaylandi"
    db.session.commit()
    flash(f"'{u.kullanici_adi}' hesabi onaylandi.", "success")
    return redirect(url_for("admin.kullanicilar"))


@admin_bp.route("/kullanicilar/<int:id>/reddet", methods=["POST"])
@admin_required
def kullanici_reddet(id):
    u = Kullanici.query.get_or_404(id)
    db.session.delete(u)
    db.session.commit()
    flash("Kayit talebi reddedildi ve silindi.", "warning")
    return redirect(url_for("admin.kullanicilar"))


@admin_bp.route("/kullanicilar/ekle", methods=["POST"])
@admin_required
def kullanici_ekle():
    kullanici_adi = request.form.get("kullanici_adi", "").strip()
    sifre = request.form.get("sifre", "")
    rol = request.form.get("rol", "magaza")
    magaza_id = request.form.get("magaza_id", type=int)
    ad_soyad = request.form.get("ad_soyad", "").strip()
    if not kullanici_adi or not sifre:
        flash("Kullanici adi ve sifre zorunlu.", "warning")
        return redirect(url_for("admin.kullanicilar"))
    if Kullanici.query.filter_by(kullanici_adi=kullanici_adi).first():
        flash("Bu kullanici adi zaten mevcut.", "warning")
        return redirect(url_for("admin.kullanicilar"))
    u = Kullanici(kullanici_adi=kullanici_adi, rol=rol,
                  magaza_id=magaza_id if rol == "magaza" else None,
                  ad_soyad=ad_soyad)
    u.set_sifre(sifre)
    db.session.add(u)
    db.session.flush()
    if rol == "magaza":
        db.session.add(KullaniciYetki(kullanici_id=u.id))
    db.session.commit()
    flash(f"Kullanici '{kullanici_adi}' olusturuldu.", "success")
    return redirect(url_for("admin.kullanicilar"))


@admin_bp.route("/kullanicilar/<int:id>/sil", methods=["POST"])
@admin_required
def kullanici_sil(id):
    u = Kullanici.query.get_or_404(id)
    if u.rol == "admin" and Kullanici.query.filter_by(rol="admin").count() == 1:
        flash("Son admin silinemez.", "danger")
        return redirect(url_for("admin.kullanicilar"))
    SatisHareketi.query.filter_by(kullanici_id=id).delete()
    SiparisTalebi.query.filter_by(kullanici_id=id).delete()
    SshBildirimi.query.filter_by(kullanici_id=id).delete()
    KullaniciYetki.query.filter_by(kullanici_id=id).delete()
    db.session.delete(u)
    db.session.commit()
    flash("Kullanici silindi.", "success")
    return redirect(url_for("admin.kullanicilar"))


@admin_bp.route("/kullanicilar/<int:id>/sifre", methods=["POST"])
@admin_required
def kullanici_sifre(id):
    u = Kullanici.query.get_or_404(id)
    yeni = request.form.get("yeni_sifre", "")
    if yeni:
        u.set_sifre(yeni)
        db.session.commit()
        flash("Sifre guncellendi.", "success")
    return redirect(url_for("admin.kullanicilar"))


# ─── YETKİ YÖNETİMİ ──────────────────────────────────────────────────────────

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
        flash(f"'{u.kullanici_adi}' yetkileri guncellendi.", "success")
        return redirect(url_for("admin.kullanicilar"))
    return render_template("admin/kullanici_yetki.html", u=u)


# ─── KATALOG ─────────────────────────────────────────────────────────────────

@admin_bp.route("/katalog")
@admin_required
def katalog():
    urunler = KatalogUrun.query.order_by(KatalogUrun.id.desc()).all()
    bekleyen_teklifler = FiyatTeklifi.query.filter_by(durum="beklemede").count()
    katalog_siparisler = (
        SiparisTalebi.query
        .filter(SiparisTalebi.notlar.like('[Katalog:%'))
        .filter(SiparisTalebi.durum.in_(["beklemede", "onaylandi"]))
        .order_by(SiparisTalebi.id.desc()).all()
    )
    return render_template("admin/katalog.html", urunler=urunler,
                           bekleyen_teklifler=bekleyen_teklifler,
                           katalog_siparisler=katalog_siparisler)


@admin_bp.route("/katalog/yeni", methods=["GET", "POST"])
@admin_required
def katalog_yeni():
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
                izin = KatalogMagazaIzin(
                    katalog_urun_id=ku.id,
                    magaza_id=int(mid),
                    fiyat_gorunsun=f"fiyat_{mid}" in request.form
                )
                db.session.add(izin)
        from flask import current_app
        upload_dir = os.path.join(current_app.root_path, "static", "katalog")
        os.makedirs(upload_dir, exist_ok=True)
        for i, f in enumerate(request.files.getlist("resimler")):
            if f and f.filename and izin_verilen(f.filename):
                ext = f.filename.rsplit(".", 1)[1].lower()
                dosya_adi = f"{uuid.uuid4().hex}.{ext}"
                f.save(os.path.join(upload_dir, dosya_adi))
                db.session.add(KatalogResim(urun_id=ku.id, dosya_adi=dosya_adi, sira=i))
        db.session.commit()
        flash("Katalog urunu eklendi.", "success")
        return redirect(url_for("admin.katalog"))
    return render_template("admin/katalog_form.html", ku=None, magazalar=magazalar)


@admin_bp.route("/katalog/<int:id>/duzenle", methods=["GET", "POST"])
@admin_required
def katalog_duzenle(id):
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
                izin = KatalogMagazaIzin(
                    katalog_urun_id=ku.id,
                    magaza_id=int(mid),
                    fiyat_gorunsun=f"fiyat_{mid}" in request.form
                )
                db.session.add(izin)
        from flask import current_app
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
        flash("Katalog urunu guncellendi.", "success")
        return redirect(url_for("admin.katalog"))
    return render_template("admin/katalog_form.html", ku=ku, magazalar=magazalar)


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
    flash("Katalog urunu silindi.", "success")
    return redirect(url_for("admin.katalog"))


@admin_bp.route("/katalog/<int:id>/fiyat-onayla", methods=["POST"])
@admin_required
def katalog_fiyat_onayla(id):
    ku = KatalogUrun.query.get_or_404(id)
    ku.fiyat_onaylandi = not ku.fiyat_onaylandi
    db.session.commit()
    durum = "onaylandi" if ku.fiyat_onaylandi else "geri alindi"
    flash(f"Fiyat gorunurlugu {durum}.", "success")
    return redirect(url_for("admin.katalog"))


@admin_bp.route("/katalog/fiyat-teklifleri")
@admin_required
def fiyat_teklifleri():
    durum = request.args.get("durum", "beklemede")
    q = FiyatTeklifi.query
    if durum != "tumu":
        q = q.filter_by(durum=durum)
    teklifler = q.order_by(FiyatTeklifi.id.desc()).all()
    bekleyen_sayi = FiyatTeklifi.query.filter_by(durum="beklemede").count()
    return render_template("admin/fiyat_teklifleri.html", teklifler=teklifler,
                           durum_filter=durum, bekleyen_sayi=bekleyen_sayi)


@admin_bp.route("/katalog/fiyat-teklifleri/<int:id>/yanitla", methods=["POST"])
@admin_required
def fiyat_teklifi_yanitla(id):
    ft = FiyatTeklifi.query.get_or_404(id)
    teklif_fiyati = request.form.get("admin_teklif_fiyati", type=float)
    admin_notu = request.form.get("admin_notu", "").strip()
    ft.admin_teklif_fiyati = teklif_fiyati
    ft.admin_notu = admin_notu
    ft.durum = "yanitlandi"
    ft.yanitlama_tarihi = datetime.now().strftime("%Y-%m-%d %H:%M")
    db.session.commit()
    flash("Fiyat teklifi yanitlandi.", "success")
    return redirect(url_for("admin.fiyat_teklifleri"))
