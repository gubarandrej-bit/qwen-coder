# -*- coding: utf-8 -*-
"""Фабрика Flask-приложения."""
import os

from flask import Flask

from app.config import Config
from app.extensions import db, login_manager


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    db_path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(uid):
        return db.session.get(User, int(uid))

    from app.routes.auth import bp as auth_bp
    from app.routes.main import bp as main_bp
    from app.routes.api import bp as api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.context_processor
    def inject_globals():
        from datetime import datetime
        return {"now": datetime.now()}

    with app.app_context():
        db.create_all()
        _seed(app)

    # планировщик периодической проверки актуальности НТД
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        if not getattr(app, "_ntd_scheduler", None):
            sched = BackgroundScheduler(daemon=True)

            def refresh_ntd():
                from app.services.ntd_check import check_document
                from app.models import NtdDocument
                with app.app_context():
                    for d in db.session.query(NtdDocument).all():
                        res = check_document(d.code)
                        d.status = res["status"]
                        d.replaced_by = res["replaced_by"]
                        d.notes = res["note"]
                        d.checked_at = res["checked_at"]
                    db.session.commit()

            if app.config.get("NTD_CHECK_ENABLED"):
                sched.add_job(refresh_ntd, "interval",
                              hours=app.config.get("NTD_CHECK_INTERVAL_HOURS", 24),
                              id="ntd_refresh")
                sched.start()
            app._ntd_scheduler = sched
    except Exception:  # noqa: BLE001 — планировщик не критичен для работы UI
        pass

    return app


def _seed(app):
    """Создание администратора и базовых данных при первом запуске."""
    from app.models import User, LlmModel
    from app.seed_ntd import seed_ntd

    if not User.query.filter_by(username=app.config["ADMIN_USERNAME"]).first():
        u = User(username=app.config["ADMIN_USERNAME"], role="admin",
                 full_name="Администратор системы")
        u.set_password(app.config["ADMIN_PASSWORD"])
        db.session.add(u)

    if not LlmModel.query.first():
        defaults = [
            # локальные бесплатные модели (рекомендованы для CPU-сервера)
            ("qwen2.5:3b", "Qwen2.5 3B (локальная, Ollama)", "local", 8192),
            ("phi3:mini", "Phi-3 mini 3.8B (локальная, Ollama)", "local", 8192),
            ("llama3.1:8b", "Llama 3.1 8B (локальная, Ollama)", "local", 8192),
            # облачные бесплатные модели (OpenRouter free / Groq / Together)
            ("meta-llama/llama-3.3-70b-instruct:free",
             "Llama 3.3 70B Free (OpenRouter)", "cloud", 32768),
            ("deepseek/deepseek-chat-v3-0324:free",
             "DeepSeek V3 Free (OpenRouter)", "cloud", 65536),
            ("google/gemini-2.0-flash-exp:free",
             "Gemini 2.0 Flash Exp Free (OpenRouter)", "cloud", 32768),
            ("qwen/qwen-2.5-72b-instruct:free",
             "Qwen2.5 72B Free (OpenRouter)", "cloud", 32768),
        ]
        for name, disp, prov, ctx in defaults:
            db.session.add(LlmModel(name=name, display_name=disp, provider=prov,
                                    context_tokens=ctx, enabled=True,
                                    added_by="system"))
    db.session.commit()
    seed_ntd(db)
