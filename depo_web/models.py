from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class Sehir(db.Model):
    __tablename__ = "sehirler"
    id = db.Column(db.Integer, primary_key=True)
    ad = db.Column(db.String(100), nullable=False, unique=True)
    magazalar = db.relationship("Magaza", backref="sehir", lazy=True)


class Magaza(db.Model):
    __tablename__ = "magazalar"
    id = db.Column(db.Integer, primary_key=True)
    ad = db.Column(db.String(150), nullable=False)
    sehir_id = db.Column(db.Integer, db.ForeignKey("sehirler.id"), nullable=False)
    adres = db.Column(db.String(300))
    telefon = db.Column(db.String(30))
    kullanicilar = db.relationship("Kullanici", backref="magaza", lazy=True)
    talepler = db.relationship("SiparisTalebi", backref="magaza", lazy=True)
    ssh_bildirimleri = db.relationship("SshBildirimi", backref="magaza", lazy=True)


class Kullanici(db.Model, UserMixin):
    __tablename__ = "kullanicilar"
    id = db.Column(db.Integer, primary_key=True)
    kullanici_adi = db.Column(db.String(80), nullable=False, unique=True)
    email = db.Column(db.String(150), nullable=True, unique=True)
    sifre_hash = db.Column(db.String(256), nullable=False)
    rol = db.Column(db.String(20), nullable=False, default="magaza")
    magaza_id = db.Column(db.Integer, db.ForeignKey("magazalar.id"), nullable=True)
    ad_soyad = db.Column(db.String(150))
    onay_durumu = db.Column(db.String(20), nullable=False, default="onaylandi")
    kayit_tarihi = db.Column(db.String(20), default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    yetki = db.relationship("KullaniciYetki", backref="kullanici", uselist=False, cascade="all, delete-orphan")

    def set_sifre(self, sifre):
        self.sifre_hash = generate_password_hash(sifre)

    def check_sifre(self, sifre):
        return check_password_hash(self.sifre_hash, sifre)

    @property
    def is_admin(self):
        return self.rol == "admin"

    def yetkisi_var_mi(self, alan):
        if self.is_admin:
            return True
        if not self.yetki:
            return False
        return getattr(self.yetki, alan, False)


class KullaniciYetki(db.Model):
    """Her mağaza kullanıcısının görebileceği bölümler."""
    __tablename__ = "kullanici_yetkileri"
    id = db.Column(db.Integer, primary_key=True)
    kullanici_id = db.Column(db.Integer, db.ForeignKey("kullanicilar.id"), nullable=False, unique=True)
    # Bölüm erişimleri
    stok = db.Column(db.Boolean, default=True)          # Stok durumum
    satis = db.Column(db.Boolean, default=True)         # Satış gir
    sevklerim = db.Column(db.Boolean, default=True)     # Sevklerim
    talepler = db.Column(db.Boolean, default=True)      # Sipariş talebi
    ssh = db.Column(db.Boolean, default=True)           # SSH bildir
    katalog = db.Column(db.Boolean, default=True)       # Katalog
    katalog_fiyat = db.Column(db.Boolean, default=True) # Fiyatları görme


class Urun(db.Model):
    __tablename__ = "urunler"
    id = db.Column(db.Integer, primary_key=True)
    kod = db.Column(db.String(50), nullable=False, unique=True)
    ad = db.Column(db.String(200), nullable=False)
    birim = db.Column(db.String(20), nullable=False, default="takim")
    paketler = db.relationship("UrunPaketi", backref="urun", lazy=True, order_by="UrunPaketi.paket_no")
    siparisler = db.relationship("Siparis", backref="urun", lazy=True)


class UrunPaketi(db.Model):
    __tablename__ = "urun_paketleri"
    id = db.Column(db.Integer, primary_key=True)
    urun_id = db.Column(db.Integer, db.ForeignKey("urunler.id"), nullable=False)
    paket_no = db.Column(db.Integer, nullable=False)
    paket_adi = db.Column(db.String(150), nullable=False)
    uretim_girisleri = db.relationship("UretimPaketGirisi", backref="paket", lazy=True)
    ssh_bildirimleri = db.relationship("SshBildirimi", backref="paket", lazy=True)


class Siparis(db.Model):
    __tablename__ = "siparisler"
    id = db.Column(db.Integer, primary_key=True)
    tarih = db.Column(db.String(10), nullable=False)
    urun_id = db.Column(db.Integer, db.ForeignKey("urunler.id"), nullable=False)
    siparis_adeti = db.Column(db.Float, nullable=False)
    notlar = db.Column(db.Text)
    durum = db.Column(db.String(30), nullable=False, default="uretimde")
    uretim_girisleri = db.relationship("UretimPaketGirisi", backref="siparis", lazy=True)

    def sevk_edilebilir_takim(self):
        if not self.urun or not self.urun.paketler:
            return 0
        paket_ids = {p.id for p in self.urun.paketler}
        giris_map = {}
        for g in self.uretim_girisleri:
            giris_map[g.paket_id] = g.uretilen_miktar
        if not paket_ids:
            return 0
        miktarlar = [giris_map.get(pid, 0) for pid in paket_ids]
        return min(miktarlar) if miktarlar else 0

    def eksik_paketler(self):
        if not self.urun or not self.urun.paketler:
            return []
        giris_map = {}
        for g in self.uretim_girisleri:
            giris_map[g.paket_id] = g.uretilen_miktar
        eksik = []
        for p in self.urun.paketler:
            miktar = giris_map.get(p.id, 0)
            if miktar < self.siparis_adeti:
                eksik.append({"paket": p, "mevcut": miktar, "eksik": self.siparis_adeti - miktar})
        return eksik


class UretimPaketGirisi(db.Model):
    __tablename__ = "uretim_paket_girisleri"
    id = db.Column(db.Integer, primary_key=True)
    siparis_id = db.Column(db.Integer, db.ForeignKey("siparisler.id"), nullable=False)
    paket_id = db.Column(db.Integer, db.ForeignKey("urun_paketleri.id"), nullable=False)
    uretilen_miktar = db.Column(db.Float, nullable=False, default=0)
    guncelleme_tarihi = db.Column(db.String(20), default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))


class StokHareketi(db.Model):
    __tablename__ = "stok_hareketleri"
    id = db.Column(db.Integer, primary_key=True)
    tarih = db.Column(db.String(10), nullable=False)
    urun_id = db.Column(db.Integer, db.ForeignKey("urunler.id"), nullable=False)
    hareket_turu = db.Column(db.String(30), nullable=False)
    miktar = db.Column(db.Float, nullable=False)
    kaynak = db.Column(db.String(50))
    referans_id = db.Column(db.Integer)
    depo = db.Column(db.String(30), default="ana_depo")
    aciklama = db.Column(db.Text)
    urun = db.relationship("Urun", backref="stok_hareketleri")


class Sevk(db.Model):
    __tablename__ = "sevkler"
    id = db.Column(db.Integer, primary_key=True)
    tarih = db.Column(db.String(10), nullable=False)
    magaza_id = db.Column(db.Integer, db.ForeignKey("magazalar.id"), nullable=True)
    nakliye_ucreti = db.Column(db.Float, default=0)
    iscilik = db.Column(db.Float, default=0)
    kdv_oran = db.Column(db.Integer, default=0)
    notlar = db.Column(db.Text)
    alici_turu = db.Column(db.String(20), default="magaza")
    alici_adi = db.Column(db.Text, nullable=True)
    nakliye_goster = db.Column(db.Boolean, default=False)
    talep_id = db.Column(db.Integer, db.ForeignKey("siparis_talepleri.id"), nullable=True)
    teslim_durumu = db.Column(db.String(20), nullable=False, default="sevk_edildi")
    teslim_tarihi = db.Column(db.String(20), nullable=True)
    magaza = db.relationship("Magaza", backref="sevkler")
    kalemler = db.relationship("SevkKalemi", backref="sevk", lazy=True, cascade="all, delete-orphan")
    giderler = db.relationship("GenelGider", backref="sevk", lazy=True, cascade="all, delete-orphan")


class SevkKalemi(db.Model):
    __tablename__ = "sevk_kalemleri"
    id = db.Column(db.Integer, primary_key=True)
    sevk_id = db.Column(db.Integer, db.ForeignKey("sevkler.id"), nullable=False)
    urun_id = db.Column(db.Integer, db.ForeignKey("urunler.id"), nullable=False)
    miktar = db.Column(db.Float, nullable=False)
    urun = db.relationship("Urun")


class GenelGider(db.Model):
    __tablename__ = "genel_giderler"
    id = db.Column(db.Integer, primary_key=True)
    sevk_id = db.Column(db.Integer, db.ForeignKey("sevkler.id"), nullable=False)
    gider_turu = db.Column(db.String(80), nullable=False)
    tutar = db.Column(db.Float, default=0)
    aciklama = db.Column(db.Text)


class SiparisTalebi(db.Model):
    __tablename__ = "siparis_talepleri"
    id = db.Column(db.Integer, primary_key=True)
    tarih = db.Column(db.String(20), nullable=False, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    magaza_id = db.Column(db.Integer, db.ForeignKey("magazalar.id"), nullable=False)
    kullanici_id = db.Column(db.Integer, db.ForeignKey("kullanicilar.id"), nullable=False)
    durum = db.Column(db.String(30), nullable=False, default="beklemede")
    notlar = db.Column(db.Text)
    iptal_sebebi = db.Column(db.Text)
    kalemler = db.relationship("SiparisTalebiKalemi", backref="talep", lazy=True, cascade="all, delete-orphan")
    kullanici = db.relationship("Kullanici", backref="talepler")
    sevkler = db.relationship("Sevk", backref="talep", lazy=True)


class SiparisTalebiKalemi(db.Model):
    __tablename__ = "siparis_talebi_kalemleri"
    id = db.Column(db.Integer, primary_key=True)
    talep_id = db.Column(db.Integer, db.ForeignKey("siparis_talepleri.id"), nullable=False)
    urun_id = db.Column(db.Integer, db.ForeignKey("urunler.id"), nullable=False)
    miktar = db.Column(db.Float, nullable=False)
    urun = db.relationship("Urun")


class SatisHareketi(db.Model):
    __tablename__ = "satis_hareketleri"
    id = db.Column(db.Integer, primary_key=True)
    tarih = db.Column(db.String(10), nullable=False)
    magaza_id = db.Column(db.Integer, db.ForeignKey("magazalar.id"), nullable=False)
    kullanici_id = db.Column(db.Integer, db.ForeignKey("kullanicilar.id"), nullable=False)
    urun_id = db.Column(db.Integer, db.ForeignKey("urunler.id"), nullable=False)
    miktar = db.Column(db.Float, nullable=False)
    notlar = db.Column(db.Text)
    urun = db.relationship("Urun", backref="satis_hareketleri")
    magaza = db.relationship("Magaza", backref="satis_hareketleri")
    kullanici = db.relationship("Kullanici", backref="satis_hareketleri")


class SshBildirimi(db.Model):
    __tablename__ = "ssh_bildirimleri"
    id = db.Column(db.Integer, primary_key=True)
    tarih = db.Column(db.String(20), nullable=False, default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    magaza_id = db.Column(db.Integer, db.ForeignKey("magazalar.id"), nullable=False)
    kullanici_id = db.Column(db.Integer, db.ForeignKey("kullanicilar.id"), nullable=False)
    urun_id = db.Column(db.Integer, db.ForeignKey("urunler.id"), nullable=False)
    paket_id = db.Column(db.Integer, db.ForeignKey("urun_paketleri.id"), nullable=True)
    hasar_aciklamasi = db.Column(db.Text, nullable=False)
    talep_miktar = db.Column(db.Float, nullable=False, default=1)
    durum = db.Column(db.String(30), nullable=False, default="beklemede")
    admin_notu = db.Column(db.Text)
    urun = db.relationship("Urun", backref="ssh_bildirimleri")
    kullanici = db.relationship("Kullanici", backref="ssh_bildirimleri")


# ─── KATALOG ──────────────────────────────────────────────────────────────────

class KatalogUrun(db.Model):
    __tablename__ = "katalog_urunler"
    id = db.Column(db.Integer, primary_key=True)
    ad = db.Column(db.String(200), nullable=False)
    kod = db.Column(db.String(80))
    kategori = db.Column(db.String(100))
    aciklama = db.Column(db.Text)
    # Ölçüler (cm)
    boy = db.Column(db.Float)
    en = db.Column(db.Float)
    derinlik = db.Column(db.Float)
    agirlik = db.Column(db.Float)
    # Fiyat
    fiyat = db.Column(db.Float)
    fiyat_onaylandi = db.Column(db.Boolean, default=False)  # Admin onayladıktan sonra görünür
    # Görünürlük
    gorunurluk = db.Column(db.String(20), default="herkes")  # herkes | secili | gizli
    # Resimler
    resimler = db.relationship("KatalogResim", backref="urun", lazy=True, cascade="all, delete-orphan")
    # Seçili mağazalar (gorunurluk='secili' ise)
    magaza_izinleri = db.relationship("KatalogMagazaIzin", backref="urun", lazy=True, cascade="all, delete-orphan")
    aktif = db.Column(db.Boolean, default=True)
    eklenme_tarihi = db.Column(db.String(20), default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))


class KatalogResim(db.Model):
    __tablename__ = "katalog_resimler"
    id = db.Column(db.Integer, primary_key=True)
    urun_id = db.Column(db.Integer, db.ForeignKey("katalog_urunler.id"), nullable=False)
    dosya_adi = db.Column(db.String(200), nullable=False)
    sira = db.Column(db.Integer, default=0)


class KatalogMagazaIzin(db.Model):
    """gorunurluk='secili' durumunda hangi mağazalar görebilir."""
    __tablename__ = "katalog_magaza_izinleri"
    id = db.Column(db.Integer, primary_key=True)
    katalog_urun_id = db.Column(db.Integer, db.ForeignKey("katalog_urunler.id"), nullable=False)
    magaza_id = db.Column(db.Integer, db.ForeignKey("magazalar.id"), nullable=False)
    fiyat_gorunsun = db.Column(db.Boolean, default=False)


class FiyatTeklifi(db.Model):
    __tablename__ = "fiyat_teklifleri"
    id = db.Column(db.Integer, primary_key=True)
    tarih = db.Column(db.String(20), default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    katalog_urun_id = db.Column(db.Integer, db.ForeignKey("katalog_urunler.id"), nullable=False)
    magaza_id = db.Column(db.Integer, db.ForeignKey("magazalar.id"), nullable=False)
    kullanici_id = db.Column(db.Integer, db.ForeignKey("kullanicilar.id"), nullable=False)
    miktar = db.Column(db.Integer, default=1)
    not_ = db.Column(db.Text)
    durum = db.Column(db.String(20), default="beklemede")  # beklemede | yanitlandi
    admin_teklif_fiyati = db.Column(db.Float)
    admin_notu = db.Column(db.Text)
    yanitlama_tarihi = db.Column(db.String(20))
    # İlişkiler
    urun = db.relationship("KatalogUrun", backref="fiyat_teklifleri", lazy=True)
    magaza = db.relationship("Magaza", backref="fiyat_teklifleri", lazy=True)
    kullanici = db.relationship("Kullanici", backref="fiyat_teklifleri", lazy=True)
