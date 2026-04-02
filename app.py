from flask import Flask, redirect, url_for, render_template
from flask_login import LoginManager, current_user
from datetime import datetime
from config import Config
from models import db, Kullanici
from auth import auth_bp
from admin import admin_bp
from magaza import magaza_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    login_manager = LoginManager(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Lutfen giris yapin."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id):
        return Kullanici.query.get(int(user_id))

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(magaza_bp, url_prefix="/m")

    @app.after_request
    def set_utf8(response):
        if response.content_type.startswith("text/html"):
            response.headers["Content-Type"] = "text/html; charset=utf-8"
        return response

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        db.session.rollback()
        return render_template("errors/500.html"), 500

    @app.context_processor
    def inject_globals():
        from models import SiparisTalebi, Kullanici
        bekleyen = 0
        bekleyen_kayit = 0
        if current_user.is_authenticated and current_user.is_admin:
            try:
                bekleyen = SiparisTalebi.query.filter_by(durum="beklemede").count()
                bekleyen_kayit = Kullanici.query.filter_by(onay_durumu="beklemede").count()
            except Exception:
                pass
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
    from models import db
    migrations = [
        ("kullanicilar", "telefon", "ALTER TABLE kullanicilar ADD COLUMN telefon TEXT"),
        ("sevkler", "kdv_oran", "ALTER TABLE sevkler ADD COLUMN kdv_oran INTEGER DEFAULT 0"),
        ("sevkler", "alici_turu", "ALTER TABLE sevkler ADD COLUMN alici_turu TEXT DEFAULT 'magaza'"),
        ("sevkler", "alici_adi", "ALTER TABLE sevkler ADD COLUMN alici_adi TEXT"),
        ("sevkler", "nakliye_goster", "ALTER TABLE sevkler ADD COLUMN nakliye_goster INTEGER DEFAULT 0"),
        ("sevkler", "talep_id", "ALTER TABLE sevkler ADD COLUMN talep_id INTEGER"),
        ("sevkler", "teslim_durumu", "ALTER TABLE sevkler ADD COLUMN teslim_durumu TEXT DEFAULT 'sevk_edildi'"),
        ("sevkler", "teslim_tarihi", "ALTER TABLE sevkler ADD COLUMN teslim_tarihi TEXT"),
        ("siparis_talepleri", "iptal_sebebi", "ALTER TABLE siparis_talepleri ADD COLUMN iptal_sebebi TEXT"),
        ("siparis_talepleri", "notlar", "ALTER TABLE siparis_talepleri ADD COLUMN notlar TEXT"),
        ("magazalar", "telefon", "ALTER TABLE magazalar ADD COLUMN telefon TEXT"),
    ]
    for tablo, sutun, sql in migrations:
        try:
            cols = [r[1] for r in db.session.execute(text(f"PRAGMA table_info({tablo})"))]
            if sutun not in cols:
                db.session.execute(text(sql))
                db.session.commit()
                print(f"Migration: {tablo}.{sutun} eklendi")
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
        print("Varsayilan admin olusturuldu: kadirkaraduman359@gmail.com / derdo541")


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)
