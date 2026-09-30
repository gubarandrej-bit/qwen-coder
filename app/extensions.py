# -*- coding: utf-8 -*-
"""Расширения Flask (отдельный модуль во избежание циклических импортов)."""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Войдите в систему для продолжения работы."
login_manager.login_message_category = "warning"
