from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from models import db, Kullanici, Magaza, Sehir
from datetime import datetime

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for("admin.dashboard"))
        return redirect(url_for("magaza.dashboard"))

    if request.method == "POST":
        tip = request.form.get("tip", "magaza")

        if tip == "yonetici":
            email = request.form.get("email", "").strip().lower()
            sifre = request.form.get("sifre", "")
            user = Kullanici.query.filter_by(email=email, rol="admin").first()
            if user and user.check_sifre(sifre):
                login_user(user)
                return redirect(url_for("admin.dashboard"))
            flash("E-posta veya şifre hatalı.", "danger")
            return render_template("login.html", aktif_sekme="yonetici")

        else:
            kullanici_adi = request.form.get("kullanici_adi", "").strip()
            sifre = request.form.get("sifre", "")
            user = Kullanici.query.filter_by(kullanici_adi=kullanici_adi, rol="magaza").first()
            if user and user.check_sifre(sifre):
                if user.onay_durumu == "beklemede":
                    flash("Hesabınız henüz onaylanmadı. Yönetici onayı bekleniyor.", "warning")
                    return render_template("login.html", aktif_sekme="magaza")
                login_user(user)
                return redirect(url_for("magaza.dashboard"))
            flash("Kullanıcı adı veya şifre hatalı.", "danger")
            return render_template("login.html", aktif_sekme="magaza")

    return render_template("login.html", aktif_sekme="yonetici")


@auth_bp.route("/kayit", methods=["POST"])
def kayit():
    ad_soyad = request.form.get("ad_soyad", "").strip()
    kullanici_adi = request.form.get("kullanici_adi", "").strip()
    sifre = request.form.get("sifre", "")
    sifre2 = request.form.get("sifre2", "")
    magaza_adi = request.form.get("magaza_adi", "").strip()
    sehir_adi = request.form.get("sehir_adi", "").strip()
    telefon = request.form.get("telefon", "").strip()

    if not all([ad_soyad, kullanici_adi, sifre, magaza_adi, sehir_adi]):
        flash("Tüm zorunlu alanları doldurunuz.", "danger")
        return render_template("login.html", aktif_sekme="kayit")

    if sifre != sifre2:
        flash("Şifreler eşleşmiyor.", "danger")
        return render_template("login.html", aktif_sekme="kayit")

    if len(sifre) < 4:
        flash("Şifre en az 4 karakter olmalı.", "danger")
        return render_template("login.html", aktif_sekme="kayit")

    if Kullanici.query.filter_by(kullanici_adi=kullanici_adi).first():
        flash("Bu kullanıcı adı zaten alınmış.", "warning")
        return render_template("login.html", aktif_sekme="kayit")

    # Şehri bul veya oluştur
    sehir = Sehir.query.filter_by(ad=sehir_adi.upper()).first()
    if not sehir:
        sehir = Sehir(ad=sehir_adi.upper())
        db.session.add(sehir)
        db.session.flush()

    # Mağazayı bul veya oluştur
    magaza = Magaza.query.filter_by(ad=magaza_adi, sehir_id=sehir.id).first()
    if not magaza:
        magaza = Magaza(ad=magaza_adi, sehir_id=sehir.id, telefon=telefon)
        db.session.add(magaza)
        db.session.flush()

    u = Kullanici(
        kullanici_adi=kullanici_adi,
        rol="magaza",
        ad_soyad=ad_soyad,
        magaza_id=magaza.id,
        onay_durumu="beklemede",
        kayit_tarihi=datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    u.set_sifre(sifre)
    db.session.add(u)
    db.session.commit()

    flash(f"Kayıt talebiniz alındı. Yönetici onayı bekleniyor.", "success")
    return render_template("login.html", aktif_sekme="magaza")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
