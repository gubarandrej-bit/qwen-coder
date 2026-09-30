# -*- coding: utf-8 -*-
"""Аутентификация: вход/выход, управление пользователями (админ)."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db
from app.models import User

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()
        if user is None or not user.check_password(password):
            flash("Неверный логин или пароль.", "danger")
            return render_template("login.html")
        if user.is_blocked:
            flash("Учетная запись заблокирована. Обратитесь к администратору.", "warning")
            return render_template("login.html")
        login_user(user, remember=bool(request.form.get("remember")))
        nxt = request.args.get("next") or url_for("main.dashboard")
        if not nxt.startswith(("/", "http")):
            nxt = url_for("main.dashboard")
        return redirect(nxt)
    return render_template("login.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Вы вышли из системы.", "info")
    return redirect(url_for("auth.login"))


# ------------------------------------------------------------------ admin
@bp.route("/admin/users")
@login_required
def users():
    if not current_user.is_admin:
        flash("Доступ только для администратора.", "danger")
        return redirect(url_for("main.dashboard"))
    return render_template("users.html", users=User.query.order_by(User.username).all())


@bp.route("/admin/users/add", methods=["POST"])
@login_required
def user_add():
    if not current_user.is_admin:
        return redirect(url_for("main.dashboard"))
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    role = request.form.get("role") or "user"
    if not username or len(password) < 6:
        flash("Логин обязателен; пароль не короче 6 символов.", "danger")
        return redirect(url_for("auth.users"))
    if User.query.filter_by(username=username).first():
        flash("Такой логин уже существует.", "danger")
        return redirect(url_for("auth.users"))
    u = User(username=username, role=role if role in ("admin", "user") else "user",
             full_name=request.form.get("full_name") or "",
             email=request.form.get("email") or "")
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    flash(f"Пользователь '{username}' создан.", "success")
    return redirect(url_for("auth.users"))


@bp.route("/admin/users/<int:uid>/delete", methods=["POST"])
@login_required
def user_delete(uid):
    if not current_user.is_admin:
        return redirect(url_for("main.dashboard"))
    u = db.session.get(User, uid)
    if not u:
        flash("Пользователь не найден.", "warning")
    elif u.id == current_user.id:
        flash("Нельзя удалить собственного администратора.", "danger")
    else:
        db.session.delete(u)
        db.session.commit()
        flash(f"Пользователь '{u.username}' удален.", "success")
    return redirect(url_for("auth.users"))


@bp.route("/admin/users/<int:uid>/password", methods=["POST"])
@login_required
def user_password(uid):
    if not current_user.is_admin:
        return redirect(url_for("main.dashboard"))
    u = db.session.get(User, uid)
    newpass = request.form.get("new_password") or request.form.get("password") or ""
    confirm = request.form.get("password2") or newpass
    if not u:
        flash("Пользователь не найден.", "warning")
    elif len(newpass) < 6:
        flash("Пароль не короче 6 символов.", "danger")
    elif newpass != confirm:
        flash("Пароли не совпадают.", "danger")
    else:
        u.set_password(newpass)
        db.session.commit()
        flash(f"Пароль пользователя '{u.username}' изменен.", "success")
    return redirect(url_for("auth.users"))


@bp.route("/admin/users/<int:uid>/block", methods=["POST"])
@login_required
def user_block(uid):
    if not current_user.is_admin:
        return redirect(url_for("main.dashboard"))
    u = db.session.get(User, uid)
    if not u:
        flash("Пользователь не найден.", "warning")
    elif u.id == current_user.id:
        flash("Нельзя заблокировать самого себя.", "danger")
    else:
        u.is_blocked = not u.is_blocked
        db.session.commit()
        flash(f"Пользователь '{u.username}': " +
              ("заблокирован." if u.is_blocked else "разблокирован."), "info")
    return redirect(url_for("auth.users"))


@bp.route("/account/password", methods=["POST"])
@login_required
def change_own_password():
    old = request.form.get("old_password") or ""
    new = request.form.get("new_password") or ""
    if not current_user.check_password(old):
        flash("Текущий пароль неверен.", "danger")
    elif len(new) < 6:
        flash("Новый пароль не короче 6 символов.", "danger")
    else:
        current_user.set_password(new)
        db.session.commit()
        flash("Пароль обновлен.", "success")
    return redirect(url_for("main.dashboard"))
