# -*- coding: utf-8 -*-
"""JSON API: загрузка файлов, запуск проверок, чат, НТД, модели (в т.ч.
ручное добавление/удаление облачных моделей через API провайдера), выгрузки."""
import io
import json
import os
import threading
import uuid
from datetime import datetime, timezone

from flask import (Blueprint, abort, current_app, jsonify, request, send_file,
                   Response)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import (Check, ChatMessage, LlmModel, NtdClause, NtdDocument,
                        UploadedFile)
from app.services import export, parser
from app.services.checks import CHECK_CATALOG, run_deterministic_checks
from app.services.llm import (LLMError, chat_answer, cloud_api_add_model,
                              cloud_api_delete_model, llm_analyze,
                              list_local_models)
from app.services.ntd_check import check_document

bp = Blueprint("api", __name__)


def _ok(**kw):
    return jsonify({"ok": True, **kw})


def _err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _log_append(check, msg):
    """Потокобезопасное добавление строки журнала (диалоговое окно прогресса)."""
    log = json.loads(check.log_json or "[]")
    log.append({"t": datetime.now().strftime("%H:%M:%S"), "msg": msg})
    check.log_json = json.dumps(log, ensure_ascii=False)
    db.session.commit()


# ============================================================ ЗАГРУЗКА =====
@bp.route("/upload", methods=["POST"])
@login_required
def upload():
    if "files" not in request.files and "file" not in request.files:
        return _err("Файлы не переданы.")
    fs = request.files.getlist("files") or [request.files["file"]]
    system = request.form.get("system") or ""
    doctype = request.form.get("doctype") or ""
    saved, notes = [], []
    for f in fs:
        fname = f.filename or ""
        ext = os.path.splitext(fname)[1].lower().lstrip(".")
        if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
            notes.append(f"Файл '{fname}' пропущен: формат .{ext} не поддерживается "
                         f"(разрешены {sorted(current_app.config['ALLOWED_EXTENSIONS'])}).")
            continue
        disk = f"{uuid.uuid4().hex}.{ext}"
        path = os.path.join(current_app.config["UPLOAD_FOLDER"], disk)
        f.save(path)
        text, tables, note = parser.extract_text(path, ext)
        # сохраняем таблицы как JSON внутри text_cache
        tc = text or ""
        if tables:
            tc += "\n@@TABLES_JSON@@\n" + json.dumps(tables, ensure_ascii=False)
        uf = UploadedFile(user_id=current_user.id, filename=disk,
                          original_name=secure_filename(fname) or fname,
                          ext=ext, size=os.path.getsize(path),
                          system=system, doctype=doctype, text_cache=tc[:2_000_000])
        db.session.add(uf)
        db.session.flush()
        saved.append({"id": uf.id, "name": uf.original_name, "note": note})
        if note:
            notes.append(f"'{fname}': {note}")
    db.session.commit()
    return _ok(saved=saved, notes=notes)


@bp.route("/files")
@login_required
def files_list():
    out = [{"id": f.id, "name": f.original_name, "ext": f.ext,
            "system": f.system, "doctype": f.doctype,
            "size": f.size, "has_text": bool((f.text_cache or "").strip())}
           for f in UploadedFile.query.order_by(UploadedFile.uploaded_at.desc()).all()]
    return _ok(files=out)


@bp.route("/files/<int:fid>/delete", methods=["POST"])
@login_required
def file_delete(fid):
    f = db.session.get(UploadedFile, fid) or abort(404)
    if not current_user.is_admin and f.user_id != current_user.id:
        abort(403)
    p = os.path.join(current_app.config["UPLOAD_FOLDER"], f.filename)
    if os.path.exists(p):
        os.remove(p)
    db.session.delete(f)
    db.session.commit()
    return _ok()


# ============================================================ ПРОВЕРКА =====
@bp.route("/check/start", methods=["POST"])
@login_required
def check_start():
    data = request.get_json(silent=True) or {}
    project = data.get("project") or "Без названия"
    mode = data.get("mode") or current_app.config["DEFAULT_MODE"]
    model_ids = data.get("models") or []
    file_ids = data.get("files") or []

    files = UploadedFile.query.filter(UploadedFile.id.in_(file_ids)).all() \
        if file_ids else []
    if not files:
        return _err("Не выбран ни один загруженный файл. Проверку проводить не на чем — "
                    "загрузите исходную документацию (xls/docx/pdf/dxf).")

    models = LlmModel.query.filter(LlmModel.id.in_(model_ids),
                                   LlmModel.enabled.is_(True)).all() \
        if model_ids else LlmModel.query.filter_by(enabled=True).all()
    if not models:
        # честно фиксируем: LLM-этап выполнен не будет
        current_app.logger.warning("Проверка запущена без выбранных моделей ИИ.")

    c = Check(user_id=current_user.id, project_name=project, mode=mode,
              status="running",
              models_used=", ".join(m.name for m in models))
    c.files = files
    db.session.add(c)
    db.session.commit()
    _log_append(c, f"Сессия проверки #{c.id} создана. Режим: {mode}. "
                   f"Проект: {project}. Файлов: {len(files)}.")
    _log_append(c, "Перечень запланированных проверок:")
    for code, title in CHECK_CATALOG:
        _log_append(c, f"  • [{code}] {title}")

    t = threading.Thread(target=_run_check, args=(current_app._get_current_object(),
                                                  c.id, [m.id for m in models]),
                         daemon=True)
    t.start()
    return _ok(check_id=c.id)


def _endpoints(app):
    """Эндпоинты в формате {provider: (base_url, api_key)} — единый вид для
    llm_analyze и chat_answer."""
    return {
        "local": (app.config["LOCAL_LLM_BASE_URL"], app.config["LOCAL_LLM_API_KEY"]),
        "cloud": (app.config["CLOUD_LLM_BASE_URL"], app.config["CLOUD_LLM_API_KEY"]),
    }


def _run_check(app, check_id, model_ids):
    with app.app_context():
        c = db.session.get(Check, check_id)
        try:
            ntd_docs = NtdDocument.query.all()
            _log_append(c, "Этап 1/4: детерминированные проверки "
                            "(сверки таблиц, расчеты по справочникам ПУЭ)…")
            det_items = run_deterministic_checks(c.files, ntd_docs)
            performed = sum(1 for i in det_items if i.performed)
            _log_append(c, f"Выполнено детерминированных проверок: {performed} из "
                            f"{len(det_items)}. Не выполненные отмечены в перечне с причинами.")

            checks_todo = [i.code for i in det_items]
            doc_ctx = build_doc_context(c.files)
            models = LlmModel.query.filter(LlmModel.id.in_(model_ids)).all()
            eps = _endpoints(app)

            _progress(c, 40)
            if models:
                _log_append(c, f"Этап 2/4: нейросетевой анализ ({c.mode}), "
                                f"моделей: {len(models)}…")
                llm_res = llm_analyze(
                    c.mode, eps["local"], eps["cloud"],
                    [f"{m.provider}:{m.name}" for m in models],
                    ntd_docs, doc_ctx, checks_todo,
                    log_cb=lambda m: _log_append(c, m))
            else:
                llm_res = {"findings": [], "not_checked": [
                    {"check": "ALL_LLM",
                     "reason": "Модели ИИ не выбраны — нейросетевой этап НЕ ВЫПОЛНЕН."}],
                    "data_needed": ["Выберите модели ИИ в разделе «Модели ИИ» и повторите проверку."],
                    "summary": "", "errors": ["Модели ИИ не выбраны."]}
                _log_append(c, "Модели ИИ не выбраны — нейросетевой этап НЕ ВЫПОЛНЕН.")
            _progress(c, 75)

            _log_append(c, "Этап 3/4: объединение результатов, классификация замечаний…")
            result = merge_results(det_items, llm_res)
            _log_append(c, "Этап 4/4: формирование отчета…")

            crit = sum(1 for f in result["findings"] if f["severity"] == "critical")
            minor = sum(1 for f in result["findings"] if f["severity"] == "minor")
            c.result_json = json.dumps(result, ensure_ascii=False)
            c.report_summary = result["summary"]
            c.critical_count, c.minor_count = crit, minor
            c.status = "done"
            c.progress = 100
            c.finished_at = datetime.now(timezone.utc)
            _log_append(c, f"Проверка завершена. Критических замечаний: {crit}, "
                            f"некритических: {minor}. Отчеты доступны для выгрузки (doc/xls).")
        except Exception as e:  # noqa: BLE001
            c.status = "error"
            _log_append(c, f"КРИТИЧЕСКАЯ ОШИБКА выполнения проверки: {e}. "
                            f"Результаты неполные; ничего не достраивалось.")
        db.session.commit()


def build_doc_context(files):
    parts = []
    for f in files:
        txt = (f.text_cache or "").split("\n@@TABLES_JSON@@")[0]
        if txt.strip():
            parts.append(f"--- ФАЙЛ: {f.original_name} ---\n{txt[:6000]}")
    if not parts:
        return ("МАШИНОЧИТАЕМЫЕ ДАННЫЕ ОТСУТСТВУЮТ: загруженные файлы не содержат "
                "извлеченного текста (DWG/DOC/скан-PDF). Автоматический содержательный "
                "анализ невозможен — сообщите об этом и запросите данные в "
                "поддерживаемом виде.")
    return "\n\n".join(parts)


def merge_results(det_items, llm_res):
    checks = [i.to_dict() for i in det_items]
    findings = []
    for i in det_items:
        findings.extend(i.findings)
    findings.extend(llm_res.get("findings", []))
    not_checked = [{"check": i.code, "title": i.title, "reason": i.reason,
                    "data_needed": i.data_needed}
                   for i in det_items if not i.performed]
    not_checked.extend(llm_res.get("not_checked", []))
    data_needed = []
    for i in det_items:
        data_needed.extend(i.data_needed)
    data_needed.extend(llm_res.get("data_needed", []))
    summary = llm_res.get("summary", "")
    perf = sum(1 for i in det_items if i.performed)
    summary = (f"Детерминированных проверок выполнено {perf} из {len(det_items)}. " + summary)
    return {"checks": checks, "findings": findings, "not_checked": not_checked,
            "data_needed": sorted(set(data_needed)), "summary": summary,
            "llm_errors": llm_res.get("errors", [])}


def _progress(c, val):
    c.progress = val
    db.session.commit()


@bp.route("/check/<int:cid>/status")
@login_required
def check_status(cid):
    c = db.session.get(Check, cid) or abort(404)
    if not current_user.is_admin and c.user_id != current_user.id:
        abort(403)
    log = json.loads(c.log_json or "[]")
    return _ok(status=c.status, progress=c.progress, log=log[-120:],
               critical=c.critical_count, minor=c.minor_count)


@bp.route("/check/<int:cid>/delete", methods=["POST"])
@login_required
def check_delete(cid):
    c = db.session.get(Check, cid) or abort(404)
    if not current_user.is_admin and c.user_id != current_user.id:
        abort(403)
    db.session.delete(c)
    db.session.commit()
    return _ok()


# ------------------------------------------------------------ ВЫГРУЗКИ ----
def _get_check(cid):
    c = db.session.get(Check, cid) or abort(404)
    if not current_user.is_admin and c.user_id != current_user.id:
        abort(403)
    return c


def _meta(c, result):
    performed = sum(1 for x in result.get("checks", []) if x.get("performed"))
    return {"project": c.project_name, "mode": c.mode, "models": c.models_used,
            "performed": performed,
            "not_performed": len(result.get("checks", [])) - performed,
            "critical": c.critical_count, "minor": c.minor_count,
            "summary": c.report_summary}


@bp.route("/check/<int:cid>/report.docx")
@login_required
def report_docx(cid):
    c = _get_check(cid)
    result = json.loads(c.result_json or "{}")
    buf = export.report_docx(result, _meta(c, result))
    name = f"Отчет_проверки_{c.id}_{secure_filename(c.project_name) or 'проект'}.docx"
    return send_file(buf, as_attachment=True, download_name=name,
                     mimetype="application/vnd.openxmlformats-officedocument."
                              "wordprocessingml.document")


@bp.route("/check/<int:cid>/report.xlsx")
@login_required
def report_xlsx(cid):
    c = _get_check(cid)
    result = json.loads(c.result_json or "{}")
    buf = export.report_xlsx(result, _meta(c, result))
    name = f"Отчет_проверки_{c.id}.xlsx"
    return send_file(buf, as_attachment=True, download_name=name,
                     mimetype="application/vnd.openxmlformats-officedocument."
                              "spreadsheetml.sheet")


@bp.route("/check/<int:cid>/bomis.xlsx")
@login_required
def bomis_xlsx(cid):
    """Ведомость объемов работ: формируется ТОЛЬКО из распознанных данных
    кабельных трасс/журнала. Если данных нет — возвращает сообщение."""
    c = _get_check(cid)
    result = json.loads(c.result_json or "{}")
    bomis = _extract_bomis(c, result)
    if not bomis:
        return _err("Недостаточно исходных данных для формирования ВОР: отсутствуют "
                    "распознанные трассы/кабельный журнал со спецификацией. "
                    "Загрузите кабельный журнал и планы в машиночитаемом виде.", 422)
    buf = export.bomis_xlsx(bomis)
    return send_file(buf, as_attachment=True, download_name=f"ВОР_{c.id}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument."
                              "spreadsheetml.sheet")


@bp.route("/check/<int:cid>/spec.xlsx")
@login_required
def spec_xlsx(cid):
    c = _get_check(cid)
    result = json.loads(c.result_json or "{}")
    items = _extract_spec(c, result)
    if not items:
        return _err("Недостаточно исходных данных для ведомости оборудования и "
                    "материалов: спецификация не распознана. Загрузите спецификацию "
                    "в форматах xls/xlsx/docx/pdf с таблицами.", 422)
    buf = export.spec_xlsx(items)
    return send_file(buf, as_attachment=True, download_name=f"ВОМиМ_{c.id}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument."
                              "spreadsheetml.sheet")


def _extract_bomis(c, result):
    """ВОР из данных кабельного журнала и трасс (только фактические объемы)."""
    from app.services.checks import classify_tables, _collect_cable_journal, \
        _collect_trace_lengths, _iter_tables  # локальные импорты служебных функций
    buckets = classify_tables(c.files)
    cj = _collect_cable_journal(buckets)
    traces = _collect_trace_lengths(buckets)
    rows = []
    for tline in traces:
        rows.append({"name": f"Прокладка кабельной линии {tline['line']} "
                             f"({tline['way'] or 'способ прокладки не указан'})",
                     "unit": "м", "qty": round(tline["length"], 2),
                     "basis": tline["file"]})
    for ccj in cj:
        rows.append({"name": f"Изготовление/маркировка кабельных линий, кабель "
                             f"{ccj['name']}",
                     "unit": "м", "qty": ccj["total_len"],
                     "basis": "кабельный журнал"})
    return rows


def _extract_spec(c, result):
    from app.services.checks import classify_tables, _collect_spec_equipment, \
        _collect_spec_cables
    buckets = classify_tables(c.files)
    items = []
    for s in _collect_spec_cables(buckets):
        items.append({"name": s["name"], "unit": "м", "qty": s["qty"],
                      "note": "из спецификации"})
    for e in _collect_spec_equipment(buckets):
        items.append({"name": e, "unit": "шт", "qty": "",
                      "note": "позиция спецификации (количество не распознано)"})
    return items


@bp.route("/history/export.xlsx")
@login_required
def history_export():
    q = Check.query
    if not current_user.is_admin:
        q = q.filter_by(user_id=current_user.id)
    checks = q.order_by(Check.started_at.desc()).all()
    buf = export.checks_history_xlsx(checks)
    return send_file(buf, as_attachment=True, download_name="Реестр_проверок.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument."
                              "spreadsheetml.sheet")


# ============================================================ ЧАТ ==========
@bp.route("/chat", methods=["POST"])
@login_required
def chat_send():
    data = request.get_json(silent=True) or {}
    question = (data.get("text") or "").strip()
    if not question:
        return _err("Пустой запрос.")
    mode = data.get("mode") or current_app.config["DEFAULT_MODE"]
    mid = data.get("model")
    m = db.session.get(LlmModel, mid) if mid else LlmModel.query.filter_by(
        provider="local" if mode != "cloud" else "cloud", enabled=True).first()
    history = [{"role": x.role, "text": x.text} for x in
               ChatMessage.query.filter_by(user_id=current_user.id)
               .order_by(ChatMessage.created_at.desc()).limit(6).all()][::-1]
    db.session.add(ChatMessage(user_id=current_user.id, role="user", text=question))
    answer = chat_answer(mode, _endpoints(current_app),
                         m.name if m else "", question, history)
    db.session.add(ChatMessage(user_id=current_user.id, role="assistant", text=answer))
    db.session.commit()
    return _ok(answer=answer, model=m.name if m else None)


# ============================================================ НТД ==========
@bp.route("/ntd", methods=["POST"])
@login_required
def ntd_add():
    if not current_user.is_admin:
        abort(403)
    d = request.get_json(silent=True) or {}
    if not d.get("code") or not d.get("title"):
        return _err("Обязательны поля: code, title.")
    doc = NtdDocument(code=d["code"], title=d["title"],
                      doc_type=d.get("doc_type", ""), category=d.get("category", ""),
                      year=d.get("year"), status=d.get("status", "не проверен"),
                      official_source=d.get("official_source", ""),
                      notes=d.get("notes", ""))
    db.session.add(doc)
    db.session.commit()
    return _ok(id=doc.id)


@bp.route("/ntd/<int:did>", methods=["PUT"])
@login_required
def ntd_update(did):
    if not current_user.is_admin:
        abort(403)
    doc = db.session.get(NtdDocument, did) or abort(404)
    d = request.get_json(silent=True) or {}
    for k in ("code", "title", "doc_type", "category", "year", "status",
              "replaced_by", "official_source", "notes"):
        if k in d:
            setattr(doc, k, d[k])
    db.session.commit()
    return _ok()


@bp.route("/ntd/<int:did>", methods=["DELETE"])
@login_required
def ntd_delete(did):
    if not current_user.is_admin:
        abort(403)
    doc = db.session.get(NtdDocument, did) or abort(404)
    db.session.delete(doc)
    db.session.commit()
    return _ok()


@bp.route("/ntd/<int:did>/clause", methods=["POST"])
@login_required
def ntd_clause_add(did):
    doc = db.session.get(NtdDocument, did) or abort(404)
    d = request.get_json(silent=True) or {}
    if not d.get("clause") or not d.get("text"):
        return _err("Обязательны поля clause, text.")
    db.session.add(NtdClause(doc_id=did, clause=d["clause"], text=d["text"],
                             topic=d.get("topic", "")))
    db.session.commit()
    return _ok()


@bp.route("/ntd/check-actuality", methods=["POST"])
@login_required
def ntd_check_actuality():
    """Ручной запуск проверки актуальности всей базы НТД."""
    docs = NtdDocument.query.all()
    updated = failed = 0
    results = []
    for doc in docs:
        res = check_document(doc.code)
        doc.status = res["status"]
        doc.replaced_by = res["replaced_by"]
        doc.notes = res["note"]
        doc.checked_at = res["checked_at"]
        results.append({"code": doc.code, "status": res["status"],
                        "replaced_by": res["replaced_by"], "note": res["note"]})
        if res["status"] == "не проверен":
            failed += 1
        else:
            updated += 1
    db.session.commit()
    return _ok(updated=updated, unconfirmed=failed, results=results)


@bp.route("/ntd/export.xlsx")
@login_required
def ntd_export():
    buf = export.ntd_base_xlsx(NtdDocument.query.all())
    return send_file(buf, as_attachment=True, download_name="База_НТД.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument."
                              "spreadsheetml.sheet")


# ============================================================ МОДЕЛИ =======
@bp.route("/models")
@login_required
def models_list():
    out = [{"id": m.id, "name": m.name, "display_name": m.display_name,
            "provider": m.provider, "enabled": bool(m.enabled),
            "context_tokens": m.context_tokens}
           for m in LlmModel.query.order_by(LlmModel.provider, LlmModel.name).all()]
    return _ok(models=out)


@bp.route("/models", methods=["POST"])
@login_required
def model_add():
    """Добавление модели вручную (администратор). Для облачных — опционально
    вызывается API провайдера (register=true)."""
    if not current_user.is_admin:
        abort(403)
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    provider = d.get("provider") or "local"
    if not name:
        return _err("Укажите имя модели.")
    if LlmModel.query.filter_by(name=name).first():
        return _err("Модель с таким именем уже есть.")
    api_note = ""
    if provider == "cloud" and d.get("register"):
        base = d.get("base_url") or current_app.config["CLOUD_LLM_BASE_URL"]
        key = d.get("api_key") or current_app.config["CLOUD_LLM_API_KEY"]
        try:
            cloud_api_add_model(base, key, name)
            api_note = "Модель зарегистрирована на стороне провайдера через API."
        except LLMError as e:
            return _err(f"Ошибка API провайдера при добавлении модели: {e}", 502)
    m = LlmModel(name=name, display_name=d.get("display_name") or name,
                 provider=provider, base_url=d.get("base_url") or "",
                 api_key_ref=d.get("api_key_ref") or "",
                 context_tokens=int(d.get("context_tokens") or 8192),
                 enabled=bool(d.get("enabled", True)),
                 added_by=current_user.username)
    db.session.add(m)
    db.session.commit()
    return _ok(id=m.id, api_note=api_note)


@bp.route("/models/<int:mid>", methods=["DELETE"])
@login_required
def model_delete(mid):
    if not current_user.is_admin:
        abort(403)
    m = db.session.get(LlmModel, mid) or abort(404)
    api_note = ""
    if m.provider == "cloud" and request.args.get("remote") == "1":
        base = m.base_url or current_app.config["CLOUD_LLM_BASE_URL"]
        key = current_app.config["CLOUD_LLM_API_KEY"]
        try:
            cloud_api_delete_model(base, key, m.name)
            api_note = "Модель удалена на стороне провайдера через API."
        except LLMError as e:
            return _err(f"Ошибка API провайдера при удалении: {e}", 502)
    db.session.delete(m)
    db.session.commit()
    return _ok(api_note=api_note)


@bp.route("/models/<int:mid>/toggle", methods=["POST"])
@login_required
def model_toggle(mid):
    if not current_user.is_admin:
        abort(403)
    m = db.session.get(LlmModel, mid) or abort(404)
    m.enabled = not m.enabled
    db.session.commit()
    return _ok(enabled=m.enabled)


@bp.route("/models/local/list")
@login_required
def models_local_list():
    """Список моделей, реально установленных на локальном сервере (Ollama)."""
    models = list_local_models(current_app.config["LOCAL_LLM_BASE_URL"],
                               current_app.config["LOCAL_LLM_API_KEY"])
    if not models:
        return _ok(models=[], note="Локальный LLM-сервер недоступен по адресу "
                    f"{current_app.config['LOCAL_LLM_BASE_URL']}. Список не пустой "
                    "получить невозможно — ничего не подставляется.")
    return _ok(models=models)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if not current_user.is_admin:
        abort(403)
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        for k in ("LOCAL_LLM_BASE_URL", "LOCAL_LLM_API_KEY",
                  "CLOUD_LLM_BASE_URL", "CLOUD_LLM_API_KEY", "DEFAULT_MODE"):
            if k in d:
                current_app.config[k] = d[k]
        return _ok(note="Настройки применены к текущему процессу. Для постоянного "
                        "эффекта пропишите значения в .env (файл конфигурации).")
    cfg = current_app.config
    return _ok(local_url=cfg["LOCAL_LLM_BASE_URL"], cloud_url=cfg["CLOUD_LLM_BASE_URL"],
               mode=cfg["DEFAULT_MODE"],
               has_cloud_key=bool(cfg["CLOUD_LLM_API_KEY"]),
               has_local_key=bool(cfg["LOCAL_LLM_API_KEY"]))
