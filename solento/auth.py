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

    if request.method == "GET":
        return render_template("login.html", aktif_sekme="yonetici")

    tip = request.form.get("tip", "yonetici")

    if tip == "yonetici":
        email = request.form.get("email", "").strip().lower()
        sifre = request.form.get("sifre", "")
        kullanici = Kullanici.query.filter(
            db.func.lower(Kullanici.email) == email,
            Kullanici.rol == "admin"
        ).first()
        if kullanici and kullanici.check_sifre(sifre):
            login_user(kullanici)
            return redirect(url_for("admin.dashboard"))
        flash("E-posta veya şifre hatalı.", "danger")
        return render_template("login.html", aktif_sekme="yonetici")

    kullanici_adi = request.form.get("kullanici_adi", "").strip()
    sifre = request.form.get("sifre", "")
    kullanici = Kullanici.query.filter_by(kullanici_adi=kullanici_adi, rol="magaza").first()
    if kullanici and kullanici.check_sifre(sifre):
        if kullanici.onay_durumu == "beklemede":
            flash("Hesabınız henüz onaylanmadı. Yönetici onayı bekleniyor.", "warning")
        else:
            login_user(kullanici)
            return redirect(url_for("magaza.dashboard"))
    else:
        flash("Kullanıcı adı veya şifre hatalı.", "danger")
    return render_template("login.html", aktif_sekme="magaza")


@auth_bp.route("/kayit", methods=["POST"])
def kayit():
    ad_soyad = request.form.get("ad_soyad", "").strip()
    kullanici_adi = request.form.get("kullanici_adi", "").strip()
    sifre = request.form.get("sifre", "")
    sifre2 = request.form.get("sifre2", "")
    telefon = request.form.get("telefon", "").strip()
    magaza_adi = request.form.get("magaza_adi", "").strip()
    sehir_adi = request.form.get("sehir_adi", "").strip()

    if not all([ad_soyad, kullanici_adi, sifre, sifre2, telefon, magaza_adi, sehir_adi]):
        flash("Tüm alanları doldurunuz.", "danger")
        return render_template("login.html", aktif_sekme="magaza")

    if sifre != sifre2:
        flash("Şifreler eşleşmiyor.", "danger")
        return render_template("login.html", aktif_sekme="magaza")

    if len(sifre) < 6:
        flash("Şifre en az 6 karakter olmalıdır.", "danger")
        return render_template("login.html", aktif_sekme="magaza")

    if Kullanici.query.filter_by(kullanici_adi=kullanici_adi).first():
        flash("Bu kullanıcı adı zaten kullanılıyor.", "danger")
        return render_template("login.html", aktif_sekme="magaza")

    sehir = Sehir.query.filter_by(ad=sehir_adi.upper()).first()
    if not sehir:
        sehir = Sehir(ad=sehir_adi.strip().upper())
        db.session.add(sehir)
        db.session.flush()

    magaza = Magaza.query.filter_by(ad=magaza_adi, sehir_id=sehir.id).first()
    if not magaza:
        magaza = Magaza(ad=magaza_adi, sehir_id=sehir.id, telefon=telefon)
        db.session.add(magaza)
        db.session.flush()

    kullanici = Kullanici(
        kullanici_adi=kullanici_adi,
        telefon=telefon,
        rol="magaza",
        magaza_id=magaza.id,
        ad_soyad=ad_soyad,
        onay_durumu="beklemede"
    )
    kullanici.set_sifre(sifre)
    db.session.add(kullanici)
    db.session.commit()

    flash("Kayıt talebiniz alındı. Yönetici onayı bekleniyor.", "success")
    return render_template("login.html", aktif_sekme="magaza")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
