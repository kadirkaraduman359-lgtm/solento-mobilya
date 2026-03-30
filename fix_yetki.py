from app import create_app
from models import db, Kullanici, KullaniciYetki

app = create_app()
with app.app_context():
    users = Kullanici.query.all()
    for u in users:
        y = u.yetki
        yetki_str = "var" if y else "YOK"
        print(u.kullanici_adi, u.rol, u.onay_durumu, yetki_str)
        if y:
            print("  talepler=%s ssh=%s satis=%s katalog=%s" % (y.talepler, y.ssh, y.satis, y.katalog))

    # Tum magaza kullanicilarinin yetkilerini ac
    for u in Kullanici.query.filter_by(rol="magaza").all():
        if not u.yetki:
            y = KullaniciYetki(kullanici_id=u.id)
            db.session.add(y)
            print("Yetki olusturuldu:", u.kullanici_adi)
        else:
            u.yetki.stok = True
            u.yetki.satis = True
            u.yetki.sevklerim = True
            u.yetki.talepler = True
            u.yetki.ssh = True
            u.yetki.katalog = True
            u.yetki.katalog_fiyat = True
            print("Yetki guncellendi:", u.kullanici_adi)
    db.session.commit()
    print("Tamam")
