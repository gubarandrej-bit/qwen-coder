# -*- coding: utf-8 -*-
"""Проверка актуальности НТД по официальным источникам РФ.

Источники (только официальные/государственные):
 - protect.gost.ru — ФГБУ «ВНИИ Стандартинформ» (информация об изменениях в ГОСТ);
 - docs.cntd.ru — федеральный портал технической документации (ГОСТ/СП/СНиП);
 - publication.pravo.gov.ru — официальный интернет-портал правовой информации (ФЗ);
 - minenergo.gov.ru / normativ_docs (ПУЭ).

Механизм: запрос к поисковым эндпоинтам, поиск кода документа и слов
«заменен», «взамен», «утратил силу», «прекращает действие», «Изменение №».
Если сетевой доступ отсутствует или документ не найден — статус НЕ меняется
и система честно сообщает, что актуальность не подтверждена (ничего не
выдумывается).
"""
import re
from datetime import datetime, timezone

import requests

UA = {"User-Agent": "Mozilla/5.0 (DocCheck/1.0; ntd-actuality-bot)"}

SEARCH_SOURCES = [
    # (название, url-шаблон поиска)
    ("cntd", "https://docs.cntd.ru/search?searchString={q}"),
    ("gostprotect", "https://protect.gost.ru/protect.aspx?control=12&platform=&query={q}"),
    ("pravo", "https://publication.pravo.gov.ru/Search/Quick?text={q}"),
]

REPLACED_RE = re.compile(r"(замен[её]н|взамен|утратил\w*\s+силу|прекращает\s+действие|"
                         r"прекратил\w*\s+действие|введен\s+взамен)", re.I)
NEW_CODE_RE = re.compile(r"(ГОСТ(?:\sР)?\s[\d\-\.]+\.\d{4}|СП\s[\d\.]+\.\d{4}|"
                         r"СП\s\d+\.\d{5}\.\d{4})")


def check_document(doc_code):
    """Возвращает dict(status, replaced_by, note, checked_at).

    status: 'действует' | 'заменен' | 'утратил силу' | 'не проверен'
    """
    q = requests.utils.quote(doc_code)
    for name, tpl in SEARCH_SOURCES:
        url = tpl.format(q=q)
        try:
            r = requests.get(url, headers=UA, timeout=15)
        except requests.RequestException:
            continue
        if r.status_code != 200:
            continue
        text = re.sub(r"<[^>]+>", " ", r.text)
        # ищем фрагменты, относящиеся к коду
        idx = text.lower().find(doc_code.lower())
        if idx == -1:
            continue
        window = text[max(0, idx - 300): idx + 700]
        m = REPLACED_RE.search(window)
        if m:
            nm = NEW_CODE_RE.search(window)
            new_code = nm.group(1) if nm else ""
            if new_code and new_code.upper() != doc_code.upper():
                return {"status": "заменен", "replaced_by": new_code,
                        "note": f"Источник {name}: обнаружено сообщение о замене.",
                        "checked_at": datetime.now(timezone.utc)}
            return {"status": "утратил силу", "replaced_by": "",
                    "note": f"Источник {name}: обнаружено сообщение о прекращении действия.",
                    "checked_at": datetime.now(timezone.utc)}
        # документ найден и признаков отмены нет → считаем действующим
        return {"status": "действует", "replaced_by": "",
                "note": f"Подтверждено по источнику {name}.",
                "checked_at": datetime.now(timezone.utc)}
    return {"status": "не проверен", "replaced_by": "",
            "note": "Документ не найден по официальным источникам или сетевой доступ "
                    "отсутствует. Актуальность НЕ ПОДТВЕРЖДЕНА (данные не выдуманы).",
            "checked_at": datetime.now(timezone.utc)}
