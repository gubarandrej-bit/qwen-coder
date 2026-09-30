# -*- coding: utf-8 -*-
"""LLM-сервис: работа с локальными и облачными нейросетями.

Интеграция по OpenAI-совместимому API (/v1/chat/completions), что подходит для:
 - локальных серверов: Ollama (http://host:11434/v1), llama.cpp server, LM Studio;
 - облачных провайдеров бесплатных моделей: OpenRouter (openrouter.ai/api/v1),
   Together AI, Groq, DeepInfra и др.

Правила проверки для нейросети (вшиваются в системный промпт):
 1. Если не хватает исходных данных — ничего не придумывать, запросить недостающую информацию.
 2. Если какие-либо проверки не проводились — сообщить, что именно и по какой причине.
 3. Не придумывать — отвечать как есть.
"""
import json
import os

import requests

SYSTEM_RULES = """ТЫ — эксперт-ревизор рабочей и проектной документации по инженерным системам РФ:
электроснабжение (ЭС), электроосвещение (ЭО/ЭМ), противопожарные системы (АПС, СОУЭ, АУПТ),
сети связи (СКС, ЛВС, ВОЛС), видеонаблюдение (ВКН/ТВН), СКУД, охранная сигнализация (ОС/ТСО),
АСУ, АСУТП.

БАЗА НОРМАТИВНЫХ ДОКУМЕНТОВ (используй ТОЛЬКО их ссылки, другие не выдумывай):
{ntd_list}

ПРАВИЛА ПРОВЕРКИ (СТРОГО СОБЛЮДАЙ):
1. Если не хватает исходных данных — НИЧЕГО НЕ ПРИДУМЫВАЙ. Сформулируй запрос недостающей информации отдельным списком "ЗАПРОС ИСХОДНЫХ ДАННЫХ".
2. Если какие-либо проверки провести невозможно — честно сообщи, ЧТО ИМЕННО не проверено и ПО КАКОЙ ПРИЧИНЕ (список "НЕ ПРОВЕРЕНО").
3. Отвечай как есть, без домыслов. Все числовые выводы должны опираться только на переданные тебе данные.
4. Каждое замечание сопровождай ссылкой на пункт конкретного НТД из базы выше (если точный пункт неизвестен — указывай документ без номера пункта и помечай "уточнить пункт").
5. Разделяй замечания на КРИТИЧЕСКИЕ (нарушение обязательных требований, угроза безопасности, взаимоувязки документов нарушены) и НЕКРИТИЧЕСКИЕ (замечания, рекомендации, оформление).
6. Ответ выдай СТРОГО в JSON без пояснительного текста вокруг:
{{"findings":[{{"check":"КОД","severity":"critical|minor","message":"...","ntd_ref":"..."}}],
 "not_checked":[{{"check":"КОД","reason":"..."}}],
 "data_needed":["..."],
 "summary":"краткие выводы"}}
"""

CHECK_CODES = {
    "SCHEME_CORR": "Корректность электрических/структурных схем и подключений",
    "CJ_SPEC": "Сверка кабельного журнала со спецификацией",
    "TRACE_LEN": "Длины трасс vs кабельный журнал",
    "CABLE_MARK": "Выбор марок кабеля",
    "CABLE_SIZE": "Выбор сечения по нагрузкам",
    "POWER_CALC": "Расчеты источников питания",
    "BATTERY": "Подбор АКБ",
    "CALCS": "Проверка прилагаемых расчетов",
    "NTD_ACTUAL": "Актуальность НТД",
    "DOC_FORMAT": "Оформление документации",
    "SCHEME_EQUIP": "Оборудование на схемах vs спецификация",
}


class LLMError(Exception):
    pass


def _headers(api_key):
    return {"Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"}


def chat(base_url, api_key, model, messages, timeout=180, max_tokens=2048,
         temperature=0.1):
    """Единый вызов OpenAI-совместимого chat completion."""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    try:
        r = requests.post(url, headers=_headers(api_key), json=payload,
                          timeout=timeout)
    except requests.RequestException as e:
        raise LLMError(f"Нет связи с эндпоинтом {url}: {e}") from e
    if r.status_code != 200:
        raise LLMError(f"Ошибка API {r.status_code}: {r.text[:400]}")
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"Некорректный ответ API: {str(data)[:400]}") from e


def list_local_models(base_url, api_key="ollama"):
    """Список моделей локального сервера (Ollama native + OpenAI-совместимый)."""
    out = []
    try:
        r = requests.get(base_url.rstrip("/").replace("/v1", "") + "/api/tags",
                         timeout=10)
        if r.status_code == 200:
            out = [m["name"] for m in r.json().get("models", [])]
    except requests.RequestException:
        pass
    if not out:
        try:
            r = requests.get(base_url.rstrip("/") + "/models",
                             headers=_headers(api_key), timeout=10)
            if r.status_code == 200:
                out = [m["id"] for m in r.json().get("data", [])]
        except requests.RequestException:
            pass
    return out


def cloud_api_add_model(base_url, api_key, name, extra=None):
    """Ручное добавление облачной модели через API провайдера
    (POST {base}/models — поддерживается OpenRouter/совместимыми шлюзами)."""
    url = base_url.rstrip("/") + "/models"
    payload = {"name": name}
    if extra:
        payload.update(extra)
    r = requests.post(url, headers=_headers(api_key), json=payload, timeout=30)
    if r.status_code not in (200, 201, 204):
        raise LLMError(f"Не удалось добавить модель ({r.status_code}): {r.text[:300]}")
    return True


def cloud_api_delete_model(base_url, api_key, name):
    """Ручное удаление облачной модели через API (DELETE {base}/models/{name})."""
    url = base_url.rstrip("/") + f"/models/{requests.utils.quote(name, safe='')}"
    r = requests.delete(url, headers=_headers(api_key), timeout=30)
    if r.status_code not in (200, 202, 204):
        raise LLMError(f"Не удалось удалить модель ({r.status_code}): {r.text[:300]}")
    return True


def build_prompt(ntd_docs, doc_context, checks_todo):
    ntd_list = "\n".join(f"- {d.code} — {d.title.strip()[:120]} [{d.status}]"
                         for d in ntd_docs) or "- (база НТД пуста — сообщите об этом)"
    codes = "\n".join(f"- {k}: {v}" for k, v in CHECK_CODES.items()
                      if k in checks_todo)
    sys = SYSTEM_RULES.format(ntd_list=ntd_list) + "\nКОДЫ ПРОВЕРОК ДЛЯ ОТВЕТА:\n" + codes
    return sys


def llm_analyze(mode, local_endpoint, cloud_endpoint, models, ntd_docs,
                doc_context, checks_todo, log_cb=None):
    """Запуск нейросетевого анализа.

    mode: local | cloud | hybrid
    models: список имен моделей (для hybrid первые — local, последние — cloud
            либо каждая модель имеет provider в имени вида 'local:name').
    Возвращает dict c ключами findings/not_checked/data_needed/summary/errors.
    """
    def log(msg):
        if log_cb:
            log_cb(msg)

    prompt = build_prompt(ntd_docs, doc_context, checks_todo)
    # ограничиваем контекст под слабые CPU-модели
    ctx = doc_context[:12000]
    user_msg = ("ДОКУМЕНТАЦИЯ (фрагменты в машиночитаемом виде):\n" + ctx +
                "\n\nВЫПОЛНИ ПРОВЕРКИ и верни JSON по инструкции.")

    results = {"findings": [], "not_checked": [], "data_needed": [],
               "summary": "", "errors": []}

    targets = []  # (provider, model_name)
    for m in models:
        if ":" in m and m.split(":", 1)[0] in ("local", "cloud"):
            targets.append(tuple(m.split(":", 1)))
        elif mode == "cloud":
            targets.append(("cloud", m))
        elif mode == "hybrid":
            targets.append(("local", m))
        else:
            targets.append(("local", m))
    if mode == "hybrid" and len(targets) < 2:
        log("Гибридный режим: указано менее двух моделей — часть задач будет направлена в облако.")

    used_any = False
    for i, (provider, model) in enumerate(targets):
        base, key = (local_endpoint if provider == "local" else cloud_endpoint)
        base_url, api_key = base
        if not base_url:
            msg = f"Режим '{mode}': не задан {'локальный' if provider == 'local' else 'облачный'} эндпоинт LLM — проверка моделью '{model}' НЕ ПРОВЕДЕНА."
            log(msg)
            results["errors"].append(msg)
            continue
        try:
            log(f"LLM[{provider}]: запрос к модели '{model}'…")
            # гибридный режим: распределяем проверки между моделями
            todo = checks_todo if provider != "hybrid-split" else None
            content = chat(base_url, api_key, model,
                           [{"role": "system", "content": prompt},
                            {"role": "user", "content": user_msg}],
                           max_tokens=1536)
            parsed = _safe_json(content)
            if parsed:
                results["findings"].extend(parsed.get("findings", []))
                results["not_checked"].extend(parsed.get("not_checked", []))
                results["data_needed"].extend(parsed.get("data_needed", []))
                if parsed.get("summary"):
                    results["summary"] += (" " if results["summary"] else "") + parsed["summary"]
                used_any = True
                log(f"LLM[{provider}]: модель '{model}' вернула {len(parsed.get('findings', []))} замечаний.")
            else:
                results["errors"].append(f"Модель '{model}' вернула не-JSON; сырой ответ сохранен в журнале.")
                results["findings"].append({
                    "check": "DOC_FORMAT", "severity": "minor",
                    "message": f"Ответ модели {model} не распарсен (не JSON). Текст ответа: {content[:500]}",
                    "ntd_ref": "—"})
                used_any = True
        except LLMError as e:
            msg = f"LLM[{provider}] '{model}': {e} — соответствующие проверки НЕ ПРОВЕДЕНЫ."
            log(msg)
            results["errors"].append(msg)
            results["not_checked"].append(
                {"check": "ALL_LLM", "reason": str(e)})

    if not used_any:
        results["not_checked"].append({
            "check": "ALL_LLM",
            "reason": "Ни одна нейросеть недоступна (не заданы эндпоинты/ключи или ошибки API). "
                      "Нейросетевой этап проверки НЕ ВЫПОЛНЕН. Проверьте настройки режима работы."})
    # дедупликация
    seen, uniq = set(), []
    for f in results["findings"]:
        k = (f.get("check"), f.get("message", "")[:120])
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    results["findings"] = uniq
    return results


def _safe_json(text):
    """Извлечение JSON из ответа LLM (может быть обёрнут в ```json ... ```)."""
    if not text:
        return None
    t = text.strip()
    if "```" in t:
        import re
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
        if m:
            t = m.group(1)
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(t[start:end + 1])
    except json.JSONDecodeError:
        return None


def chat_answer(mode, endpoints, model, question, history=None):
    """Диалоговое окно: общий вопрос пользователем."""
    provider = "cloud" if mode == "cloud" else "local"
    base_url, api_key = endpoints.get(provider, ("", ""))
    if mode == "hybrid":
        base_url, api_key = endpoints["local"] if endpoints["local"][0] else endpoints["cloud"]
    if not base_url:
        return ("Эндпоинт LLM не настроен для выбранного режима. Ответ дать невозможно — "
                "проверьте раздел «Модели ИИ».")
    msgs = [{"role": "system", "content":
             "Ты — ассистент системы проверки документации по инженерным сетям РФ. "
             "Отвечай по-русски. Правила: 1) при нехватке данных не выдумывай, а запрашивай; "
             "2) о невозможности проверки сообщай прямо; 3) отвечай как есть."}]
    for h in (history or [])[-6:]:
        msgs.append({"role": h["role"], "content": h["text"]})
    msgs.append({"role": "user", "content": question})
    try:
        return chat(base_url, api_key, model, msgs, timeout=120, max_tokens=1024)
    except LLMError as e:
        return f"LLM недоступна: {e}. Ответ сгенерирован правилами системы, а не нейросетью."
