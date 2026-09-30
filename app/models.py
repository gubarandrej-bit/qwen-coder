# -*- coding: utf-8 -*-
"""Модели базы данных SQLAlchemy."""
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    full_name = db.Column(db.String(128), default="")
    email = db.Column(db.String(128), default="")
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(16), default="user")  # admin | user
    is_blocked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    checks = db.relationship("Check", backref="user", lazy="dynamic")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    @property
    def is_admin(self):
        return self.role == "admin"


class NtdDocument(db.Model):
    """Нормативно-технический документ."""
    __tablename__ = "ntd_documents"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(128), unique=True, nullable=False)   # напр. СП 6.13130.2021
    title = db.Column(db.Text, nullable=False)
    doc_type = db.Column(db.String(32), default="СП")               # ФЗ/СП/ГОСТ/ПУЭ/СНиП
    category = db.Column(db.String(64), default="общие")            # область применения
    year = db.Column(db.Integer)
    status = db.Column(db.String(32), default="действует")          # действует/заменен/утратил силу/не проверен
    replaced_by = db.Column(db.String(128), default="")             # чем заменен
    official_source = db.Column(db.String(256), default="")         # URL официального источника
    checked_at = db.Column(db.DateTime)                             # последняя проверка актуальности
    notes = db.Column(db.Text, default="")
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    clauses = db.relationship("NtdClause", backref="doc", lazy="dynamic",
                              cascade="all, delete-orphan")


class NtdClause(db.Model):
    """Конкретный пункт НТД, используемый в правилах проверки."""
    __tablename__ = "ntd_clauses"

    id = db.Column(db.Integer, primary_key=True)
    doc_id = db.Column(db.Integer, db.ForeignKey("ntd_documents.id"), nullable=False)
    clause = db.Column(db.String(64), nullable=False)   # напр. "5.2.1"
    text = db.Column(db.Text, nullable=False)
    topic = db.Column(db.String(64), default="")        # тема: кабель, сечение, СОУЭ...


class UploadedFile(db.Model):
    """Файл исходной документации, загруженный пользователем."""
    __tablename__ = "uploaded_files"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    filename = db.Column(db.String(256), nullable=False)      # имя на диске
    original_name = db.Column(db.String(256), nullable=False)  # исходное имя
    ext = db.Column(db.String(16))
    size = db.Column(db.Integer, default=0)
    system = db.Column(db.String(64), default="")   # ЭОМ, ЭС, АПС, СОУЭ, СКС, ...
    doctype = db.Column(db.String(64), default="")  # план, схема, кабельный журнал, спецификация, расчет
    uploaded_at = db.Column(db.DateTime, default=utcnow)

    text_cache = db.Column(db.Text, default="")  # извлеченный текст/таблицы (для xls/docx/pdf)


check_files = db.Table(
    "check_files", db.Model.metadata,
    db.Column("check_id", db.ForeignKey("checks.id"), primary_key=True),
    db.Column("file_id", db.ForeignKey("uploaded_files.id"), primary_key=True),
)


class LlmModel(db.Model):
    """Модель ИИ (локальная или облачная)."""
    __tablename__ = "llm_models"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)  # идентификатор для API
    display_name = db.Column(db.String(128), default="")
    provider = db.Column(db.String(16), default="local")           # local | cloud
    base_url = db.Column(db.String(256), default="")               # override для облачных
    api_key_ref = db.Column(db.String(64), default="")             # env-переменная с ключом
    enabled = db.Column(db.Boolean, default=True)
    context_tokens = db.Column(db.Integer, default=8192)
    params_json = db.Column(db.Text, default="{}")                 # temperature и т.п.
    added_by = db.Column(db.String(64), default="")
    created_at = db.Column(db.DateTime, default=utcnow)


class Check(db.Model):
    """Сессия/результат проверки документации."""
    __tablename__ = "checks"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    project_name = db.Column(db.String(256), default="")
    mode = db.Column(db.String(16), default="local")   # local | cloud | hybrid
    models_used = db.Column(db.String(256), default="")
    status = db.Column(db.String(16), default="new")   # new|running|done|error
    progress = db.Column(db.Integer, default=0)        # 0..100
    log_json = db.Column(db.Text, default="[]")        # журнал событий (диалоговое окно)
    result_json = db.Column(db.Text, default="{}")     # структурированный результат
    report_summary = db.Column(db.Text, default="")    # краткие выводы
    critical_count = db.Column(db.Integer, default=0)
    minor_count = db.Column(db.Integer, default=0)
    started_at = db.Column(db.DateTime, default=utcnow)
    finished_at = db.Column(db.DateTime)

    files = db.relationship("UploadedFile", secondary=check_files,
                            backref="checks")


class ChatMessage(db.Model):
    """Сообщения диалогового окна с системой/LLM."""
    __tablename__ = "chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    role = db.Column(db.String(16), default="user")  # user | assistant | system
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
