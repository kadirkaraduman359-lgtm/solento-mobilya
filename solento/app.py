from flask import Flask, redirect, url_for
from datetime import datetime
from flask_login import LoginManager, current_user
from models import db, Kullanici
from auth import auth_bp
from admin import admin_bp
from magaza import magaza_bp
from config import Config


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    login_manager = LoginManager(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Lütfen giriş yapın."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id):
        return Kullanici.query.get(int(user_id))

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(magaza_bp, url_prefix="/m")

    @app.context_processor
    def inject_globals():
        from models import SiparisTalebi, Kullanici as K
        bekleyen = 0
        bekleyen_kayit = 0
        if current_user.is_authenticated and current_user.is_admin:
            bekleyen = SiparisTalebi.query.filter_by(durum="beklemede").count()
            bekleyen_kayit = K.query.filter_by(onay_durumu="beklemede").count()
        return {"now": datetime.now(), "bekleyen_badge": bekleyen, "bekleyen_kayit": bekleyen_kayit}

    @app.route("/")
    def index():
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if current_user.is_admin:
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("magaza.dashboard"))

    with app.app_context():
        db.create_all()
        _auto_migrate()
        _seed_admin()

    return app


def _auto_migrate():
    from sqlalchemy import text
    migrations = [
        ("kullanicilar", "telefon", "ALTER TABLE kullanicilar ADD COLUMN telefon TEXT"),
        ("kullanicilar", "email", "ALTER TABLE kullanicilar ADD COLUMN email TEXT"),
        ("sevkler", "kdv_oran", "ALTER TABLE sevkler ADD COLUMN kdv_oran INTEGER DEFAULT 0"),
        ("sevkler", "alici_turu", "ALTER TABLE sevkler ADD COLUMN alici_turu TEXT DEFAULT 'magaza'"),
        ("sevkler", "alici_adi", "ALTER TABLE sevkler ADD COLUMN alici_adi TEXT"),
        ("sevkler", "nakliye_goster", "ALTER TABLE sevkler ADD COLUMN nakliye_goster INTEGER DEFAULT 0"),
        ("sevkler", "talep_id", "ALTER TABLE sevkler ADD COLUMN talep_id INTEGER"),
        ("sevkler", "teslim_durumu", "ALTER TABLE sevkler ADD COLUMN teslim_durumu TEXT DEFAULT 'sevk_edildi'"),
        ("sevkler", "teslim_tarihi", "ALTER TABLE sevkler ADD COLUMN teslim_tarihi TEXT"),
        ("siparis_talepleri", "iptal_sebebi", "ALTER TABLE siparis_talepleri ADD COLUMN iptal_sebebi TEXT"),
        ("siparis_talepleri", "notlar", "ALTER TABLE siparis_talepleri ADD COLUMN notlar TEXT"),
        ("urunler", "kod", "ALTER TABLE urunler ADD COLUMN kod TEXT DEFAULT ''"),
        ("urunler", "birim", "ALTER TABLE urunler ADD COLUMN birim TEXT DEFAULT 'takim'"),
        ("magazalar", "telefon", "ALTER TABLE magazalar ADD COLUMN telefon TEXT"),
        ("kullanici_yetkileri", "stok", "ALTER TABLE kullanici_yetkileri ADD COLUMN stok INTEGER DEFAULT 1"),
        ("kullanici_yetkileri", "talepler", "ALTER TABLE kullanici_yetkileri ADD COLUMN talepler INTEGER DEFAULT 1"),
        ("kullanici_yetkileri", "sevklerim", "ALTER TABLE kullanici_yetkileri ADD COLUMN sevklerim INTEGER DEFAULT 1"),
        ("kullanici_yetkileri", "ssh", "ALTER TABLE kullanici_yetkileri ADD COLUMN ssh INTEGER DEFAULT 1"),
        ("kullanici_yetkileri", "katalog", "ALTER TABLE kullanici_yetkileri ADD COLUMN katalog INTEGER DEFAULT 1"),
        ("kullanici_yetkileri", "satis", "ALTER TABLE kullanici_yetkileri ADD COLUMN satis INTEGER DEFAULT 1"),
        ("kullanici_yetkileri", "katalog_fiyat", "ALTER TABLE kullanici_yetkileri ADD COLUMN katalog_fiyat INTEGER DEFAULT 0"),
    ]
    for tablo, sutun, sql in migrations:
        try:
            cols = [r[1] for r in db.session.execute(text(f"PRAGMA table_info({tablo})"))]
            if sutun not in cols:
                db.session.execute(text(sql))
                db.session.commit()
        except Exception as e:
            print(f"Migration hatasi {tablo}.{sutun}: {e}")


def _seed_admin():
    if not Kullanici.query.filter_by(rol="admin").first():
        admin = Kullanici(
            kullanici_adi="kadir",
            email="kadirkaraduman359@gmail.com",
            rol="admin",
            ad_soyad="Kadir Karaduman",
            onay_durumu="onaylandi"
        )
        admin.set_sifre("derdo541")
        db.session.add(admin)
        db.session.commit()
        print("Admin olusturuldu: kadirkaraduman359@gmail.com / derdo541")


application = create_app()

if __name__ == "__main__":
    application.run(host="0.0.0.0", port=5000, debug=True)
