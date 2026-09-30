# -*- coding: utf-8 -*-
"""Основные страницы веб-интерфейса."""
import json
import os
from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, send_file, url_for)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import (Check, ChatMessage, LlmModel, NtdClause, NtdDocument,
                        UploadedFile, check_files)
from app.services import parser

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def index():
    return redirect(url_for("main.dashboard"))


@bp.route("/dashboard")
@login_required
def dashboard():
    checks = (Check.query.filter_by(user_id=current_user.id)
              .order_by(Check.started_at.desc()).limit(20).all())
    stats = {
        "checks": Check.query.count(),
        "docs": NtdDocument.query.count(),
        "files": UploadedFile.query.count(),
        "models": LlmModel.query.filter_by(enabled=True).count(),
    }
    return render_template("dashboard.html", checks=checks, stats=stats)


@bp.route("/upload", methods=["GET"])
@login_required
def upload_page():
    files = UploadedFile.query.order_by(UploadedFile.uploaded_at.desc()).all()
    return render_template("upload.html", files=files)


@bp.route("/ntd")
@login_required
def ntd():
    docs = NtdDocument.query.order_by(NtdDocument.doc_type, NtdDocument.code).all()
    return render_template("ntd.html", docs=docs)


@bp.route("/ntd/<int:doc_id>/clauses")
@login_required
def ntd_clauses(doc_id):
    doc = db.session.get(NtdDocument, doc_id) or abort(404)
    clauses = NtdClause.query.filter_by(doc_id=doc_id).all()
    return render_template("ntd_clauses.html", doc=doc, clauses=clauses)


@bp.route("/models")
@login_required
def models():
    local = LlmModel.query.filter_by(provider="local").all()
    cloud = LlmModel.query.filter_by(provider="cloud").all()
    return render_template("models.html", local=local, cloud=cloud,
                           mode=current_app.config["DEFAULT_MODE"],
                           local_url=current_app.config["LOCAL_LLM_BASE_URL"],
                           cloud_url=current_app.config["CLOUD_LLM_BASE_URL"])


@bp.route("/check/<int:check_id>")
@login_required
def check_view(check_id):
    c = db.session.get(Check, check_id) or abort(404)
    if not current_user.is_admin and c.user_id != current_user.id:
        abort(403)
    result = json.loads(c.result_json or "{}")
    log = json.loads(c.log_json or "[]")
    return render_template("check_result.html", c=c, result=result, log=log)


@bp.route("/history")
@login_required
def history():
    q = Check.query
    if not current_user.is_admin:
        q = q.filter_by(user_id=current_user.id)
    checks = q.order_by(Check.started_at.desc()).all()
    return render_template("history.html", checks=checks)


@bp.route("/chat")
@login_required
def chat_page():
    msgs = (ChatMessage.query.filter_by(user_id=current_user.id)
            .order_by(ChatMessage.created_at.asc()).limit(200).all())
    return render_template("chat.html", messages=msgs)
