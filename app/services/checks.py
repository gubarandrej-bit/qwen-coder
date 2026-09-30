# -*- coding: utf-8 -*-
"""Детерминированные (не-LLM) проверки документации по инженерным сетям.

Здесь выполняется то, что можно посчитать точно без нейросети:
 - сверка кабельного журнала со спецификацией (наименования/количества);
 - сверка оборудования на схемах со спецификацией;
 - проверка длин кабельных трасс на планах с учетом способов прокладки;
 - повторный расчет сечения кабеля по нагрузке (ПУЭ, допустимый длительный ток);
 - проверка расчетов источников питания (ток/мощность) и подбора АКБ;
 - сводка по всем прилагаемым расчетам.

Принципы (правила для системы):
 1. При нехватке исходных данных ничего не придумывается — проверка помечается
    как НЕ ПРОВЕДЕНА с указанием причины и запрашивается недостающая информация.
 2. Все замечания ссылаются на конкретные пункты НТД.
 3. Замечания делятся на критические (critical) и некритические (minor).
"""
import re
from decimal import Decimal, InvalidOperation

# ---------------------------------------------------------------------------
# Справочник ПУЭ изд.7, табл. 1.3.6 — допустимый длительный ток для медных
# жил, А (прокладка в воздухе / в трубе — берем более жесткое условие).
# Ключ — сечение мм2.
CU_AMPACITY = {
    0.5: 11, 0.75: 15, 1.0: 17, 1.5: 23, 2.5: 30, 4.0: 41, 6.0: 50,
    10.0: 80, 16.0: 100, 25.0: 140, 35.0: 170, 50.0: 215, 70.0: 270,
    95.0: 330, 120.0: 385, 150.0: 440, 185.0: 510, 240.0: 605,
}
AL_AMPACITY = {
    2.5: 24, 4.0: 32, 6.0: 39, 10.0: 60, 16.0: 75, 25.0: 105, 35.0: 130,
    50.0: 165, 70.0: 210, 95.0: 255, 120.0: 295, 150.0: 335, 185.0: 385,
    240.0: 455,
}

# Коэффициент запаса длины на способ прокладки (горизонтальные участки,
# проходы, запас на подключение по СП 76.13330.2016 п. 6.16 /common practice:
# запас на подключение к оборудованию 0.5–0.7 м, на прокладку в лотках/трубах
# учится по проектным данным). Здесь применяется только если в таблице есть
# явный столбец "способ прокладки" — иначе коэффициент не применяется.
LAYING_EXTRA = {
    "лоток": 1.05, "лот": 1.05, "труба": 1.07, "гофра": 1.07,
    "кабель-канал": 1.05, "кк": 1.05, "эстакада": 1.03, "воздушно": 1.02,
    "туннель": 1.03, "шахта": 1.03, "скрыто": 1.05, "открыто": 1.03,
}

NUM_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def to_num(v):
    """Извлечение числа из строки/значения ячеек таблиц."""
    if v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    m = NUM_RE.search(str(v).replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except (ValueError, InvalidOperation):
        return None


def norm_name(s):
    """Нормализация наименования для сверки (регистр, пробелы, ё->е).

    дополнительно удаляются служебные индексы исполнения в скобках:
    'ВВГнг(А)-LS' и 'ВВГнг-LS' считаются одним и тем же кабелем.
    """
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"\([^)]*\)", "", s)          # (А), (а), (В) — индексы горючести
    s = re.sub(r"[\s\-_/]+", "", s)
    return s


def find_col(header_row, keywords):
    """Поиск индекса колонки по ключевым словам заголовка."""
    for i, h in enumerate(header_row):
        hl = norm_name(h)
        for kw in keywords:
            if norm_name(kw) in hl:
                return i
    return None


def parse_table(table):
    """Возвращает (header, rows-as-dicts) по первой непустой строке-заголовку."""
    rows = table.get("rows") or []
    if len(rows) < 2:
        return None, []
    header = rows[0]
    out = []
    for r in rows[1:]:
        d = {}
        for i, h in enumerate(header):
            d[i] = r[i] if i < len(r) else ""
        d["__raw__"] = r
        out.append(d)
    return header, out


# ---------------------------------------------------------------------------
class Finding(dict):
    """Замечание/результат проверки."""

    @staticmethod
    def make(check, severity, message, ntd_ref="", details=""):
        return {"check": check, "severity": severity,  # critical | minor | info
                "message": message, "ntd_ref": ntd_ref, "details": details}


class CheckItem:
    """Описывает одну проверку: выполнена/не выполнена + находки."""

    def __init__(self, code, title):
        self.code = code
        self.title = title
        self.performed = False
        self.reason = ""          # причина, если не проводилась
        self.findings = []
        self.data_needed = []     # какая информация отсутствует

    def not_performed(self, reason, needed=None):
        self.performed = False
        self.reason = reason
        self.data_needed = needed or []

    def done(self, findings=None):
        self.performed = True
        if findings:
            self.findings.extend(findings)

    def to_dict(self):
        return {
            "code": self.code, "title": self.title,
            "performed": self.performed, "reason": self.reason,
            "data_needed": self.data_needed, "findings": self.findings,
        }


# ---------------------------------------------------------------------------
def classify_tables(uploaded_files):
    """Распределяет загруженные файлы/таблицы по типам документации.

    Возвращает dict: cable_journal[], specification[], plans[], schemes[],
    calculations[], other[] — списки (file_obj, table_or_None).
    """
    buckets = {"cable_journal": [], "specification": [], "plans": [],
               "schemes": [], "calculations": [], "loads": [], "power": [],
               "battery": [], "other": []}
    for f in uploaded_files:
        name = (f.original_name or "").lower()
        text = f.text_cache or ""
        matched = False
        if "кабель" in name and ("журнал" in name or "cj" in name) or "кабельный журнал" in text[:2000].lower():
            buckets["cable_journal"].append(f); matched = True
        if "специфик" in name or "специфик" in text[:2000].lower():
            buckets["specification"].append(f); matched = True
        if any(k in name for k in ("план", "plan", "trace", "трасса")):
            buckets["plans"].append(f); matched = True
        if any(k in name for k in ("схема", "scheme", "struct", "стр")):
            buckets["schemes"].append(f); matched = True
        if any(k in name for k in ("расчет", "raschet", "calc")):
            buckets["calculations"].append(f); matched = True
        if "нагрузк" in name or "нагрузк" in text[:2000].lower():
            buckets["loads"].append(f); matched = True
        if any(k in name for k in ("щит", "источник", "ип", "бп", "epsi")):
            buckets["power"].append(f); matched = True
        if any(k in name for k in ("акб", "аккумулятор", "батаре")):
            buckets["battery"].append(f); matched = True
        if not matched:
            buckets["other"].append(f)
    return buckets


# ---------------------------------------------------------------------------
CHECK_CATALOG = [
    ("CJ_SPEC", "Сверка кабельного журнала со спецификацией (наименования и количества кабеля)"),
    ("SCHEME_EQUIP", "Сверка оборудования на электрических/структурных схемах со спецификацией"),
    ("TRACE_LEN", "Проверка длин кабельных трасс на планах и сверка с кабельным журналом (с учетом способа прокладки)"),
    ("CABLE_MARK", "Проверка выбора марок кабелей по условиям прокладки и пожарной безопасности (ГОСТ 31565-2012, СП 6.13130.2021, ПУЭ)"),
    ("CABLE_SIZE", "Проверка выбора сечения кабелей по нагрузкам (ПУЭ табл. 1.3.6, потеря напряжения)"),
    ("POWER_CALC", "Проверка расчетов источников питания по току и мощности"),
    ("BATTERY", "Проверка правильности подбора аккумуляторных батарей"),
    ("CALCS", "Проверка всех прилагаемых расчетов (полнота, корректность методики)"),
    ("SCHEME_CORR", "Корректность электрических и структурных схем, правильность электрических подключений"),
    ("NTD_ACTUAL", "Актуальность примененных в документации нормативных документов"),
    ("DOC_FORMAT", "Соответствие оформления документации ГОСТ Р 21.101-2026, ГОСТ 21.208-2013, ГОСТ 21.210-2014, ГОСТ Р 21.703-2020"),
]


def _classify_by_doctype(files):
    """Дополнительная классификация по явному типу документа (doctype), который
    пользователь выбирает при загрузке. Позволяет корректно обрабатывать
    ситуации, когда в одном файле несколько листов (КЖ + спецификация + нагрузки)
    или название файла неинформативно."""
    b = {"cable_journal": [], "specification": [], "plans": [], "schemes": [],
         "calculations": [], "loads": [], "power": [], "battery": []}
    keymap = {
        "кабельный журнал": "cable_journal", "кж": "cable_journal",
        "спецификация": "specification", "ведомость материалов": "specification",
        "план": "plans", "трассы": "plans",
        "схема": "schemes", "расчет": "calculations",
        "нагрузки": "loads", "источник питания": "power", "акб": "battery",
    }
    for f in files:
        d = norm_name(f.doctype or "")
        if not d:
            continue
        for kw, bucket in keymap.items():
            if norm_name(kw) in d:
                b[bucket].append(f)
                break
    return b


def run_deterministic_checks(files, ntd_docs):
    """Выполняет все возможные детерминированные проверки.

    files: список UploadedFile (с заполненным text_cache и tables через parser).
    Возвращает список CheckItem.
    """
    items = {code: CheckItem(code, title) for code, title in CHECK_CATALOG}
    buckets = classify_tables(files)
    # объединяем эвристическую классификацию с явным типом документа от пользователя
    for k, v in _classify_by_doctype(files).items():
        seen = {id(f) for f in buckets[k]}
        buckets[k] = buckets[k] + [f for f in v if id(f) not in seen]

    # -------- CJ vs SPECIFICATION -----------------------------------------
    cj_items = _collect_cable_journal(buckets)
    spec_cables = _collect_spec_cables(buckets)
    it = items["CJ_SPEC"]
    if not cj_items:
        it.not_performed(
            "Кабельный журнал не обнаружен среди загруженных файлов либо не может быть разобран.",
            ["Файл кабельного журнала (xls/xlsx/docx/pdf с таблицами): наименование кабеля, длина, количество."]
        )
    elif not spec_cables:
        it.not_performed(
            "Спецификация (раздел 'Материалы'/'Кабельная продукция') не обнаружена.",
            ["Файл спецификации с наименованиями и количеством кабеля."]
        )
    else:
        findings = _compare_cables(cj_items, spec_cables)
        it.done(findings)

    # -------- Схемы vs спецификация (оборудование) ------------------------
    it = items["SCHEME_EQUIP"]
    scheme_names = _collect_scheme_equipment(buckets)
    spec_equip = _collect_spec_equipment(buckets)
    if not scheme_names:
        it.not_performed(
            "Не найдено распознанное оборудование на схемах (чертежи DWG не разбираются автоматически, "
            "либо на схемах нет машиночитаемых обозначений).",
            ["Схемы в машиночитаемом виде (PDF с текстом/DXF/таблица элементов схемы), либо перечень оборудования со схем."]
        )
    elif not spec_equip:
        it.not_performed("Спецификация оборудования не обнаружена.",
                         ["Спецификация оборудования, материалов и изделий."])
    else:
        findings = _compare_equipment(scheme_names, spec_equip)
        it.done(findings)

    # -------- Длины трасс ---------------------------------------------------
    it = items["TRACE_LEN"]
    trace_lengths = _collect_trace_lengths(buckets)
    if not trace_lengths:
        it.not_performed(
            "Длины трасс на планах не извлечены: планы в DWG не разбираются без конвертера, "
            "или на планах отсутствуют размерные таблицы трасс.",
            ["План трасс в DXF/PDF с таблицей длин, либо ведомость длин трасс (xlsx) со столбцами: линия, трасса, длина, способ прокладки."]
        )
    elif not cj_items:
        it.not_performed("Кабельный журнал отсутствует — сверять длины не с чем.",
                         ["Кабельный журнал."])
    else:
        findings = _compare_traces_vs_journal(trace_lengths, cj_items)
        it.done(findings)

    # -------- Марка кабеля ---------------------------------------------------
    it = items["CABLE_MARK"]
    all_cables = sorted({c["name_norm"] and c["name"] for c in cj_items} |
                        {c["name"] for c in spec_cables})
    if not all_cables:
        it.not_performed("Нет данных о марках кабелей (отсутствуют кабельный журнал и спецификация).",
                         ["Кабельный журнал или спецификация с марками кабеля."])
    else:
        findings = _check_cable_marks(all_cables, buckets)
        it.done(findings)

    # -------- Сечение по нагрузкам ------------------------------------------
    it = items["CABLE_SIZE"]
    loads = _collect_loads(buckets)
    if not loads:
        it.not_performed(
            "Нет данных о нагрузках: таблица нагрузок/расчет токов не обнаружена среди загруженных файлов.",
            ["Таблица нагрузок: линия, мощность (кВт) или ток (А), напряжение, косинус φ, выбранный кабель и его сечение."]
        )
    else:
        findings = _check_cable_sizes(loads)
        it.done(findings)

    # -------- Источники питания ---------------------------------------------
    it = items["POWER_CALC"]
    power_calc = _collect_power_calcs(buckets)
    if not power_calc:
        it.not_performed(
            "Расчеты источников питания (номинальный ток, мощность, баланс) не обнаружены.",
            ["Расчет ИП/ЩП/ИБП: суммарная мощность нагрузки, расчетный ток, номинал автомата/предохранителя."]
        )
    else:
        findings = _check_power_calcs(power_calc)
        it.done(findings)

    # -------- АКБ ------------------------------------------------------------
    it = items["BATTERY"]
    batt = _collect_battery_calcs(buckets)
    if not batt:
        it.not_performed(
            "Расчет подбора АКБ не обнаружен.",
            ["Расчет емкости АКБ: ток нагрузки, время автономной работы, end-of-discharge напряжение, температура."]
        )
    else:
        findings = _check_battery(batt)
        it.done(findings)

    # -------- Прочие расчеты --------------------------------------------------
    it = items["CALCS"]
    calcs = buckets["calculations"]
    if not calcs:
        it.not_performed("Файлы с расчетами не загружены.",
                         ["Файлы расчетов (потери напряжения, токи КЗ, молезащита, освещенность и т.п.)."])
    else:
        findings = [Finding.make("CALCS", "info",
                                 f"Обнаружен файл расчета: {f.original_name}. Проверка методики и чисел будет выполнена нейросетью на этапе LLM-анализа.")]
        for f in calcs:
            if not (f.text_cache or "").strip():
                findings.append(Finding.make(
                    "CALCS", "minor",
                    f"Расчет '{f.original_name}' не содержит машиночитаемых данных — автоматическая проверка невозможна.",
                    ntd_ref="—"))
        it.done(findings)

    # -------- Актуальность НТД -------------------------------------------------
    it = items["NTD_ACTUAL"]
    outdated = [d for d in ntd_docs if d.status in ("заменен", "утратил силу")]
    unchecked = [d for d in ntd_docs if not d.checked_at]
    findings = []
    for d in outdated:
        findings.append(Finding.make(
            "NTD_ACTUAL", "critical",
            f"В базе значится документ '{d.code}' со статусом '{d.status}'"
            + (f" (действует: {d.replaced_by})" if d.replaced_by else "") +
            ". Применение замененного документа в проектной/рабочей документации не допускается.",
            ntd_ref=d.code))
    if not ntd_docs:
        it.not_performed("База НТД пуста.", ["Заполните базу нормативно-технических документов."])
    else:
        if unchecked:
            findings.append(Finding.make(
                "NTD_ACTUAL", "minor",
                f"{len(unchecked)} документ(ов) не имели подтверждения актуальности (автоматическая проверка не выполнялась/нет доступа к официальному источнику): "
                + ", ".join(d.code for d in unchecked[:10]),
                ntd_ref="—"))
        it.done(findings)

    # -------- Проверки, требующие LLM/чертежи --------------------------------
    for code, reason, need in [
        ("SCHEME_CORR",
         "Проверка топологии электрических/структурных схем требует анализа чертежей (DWG) и выполняется нейросетью при наличии машиночитаемого описания схем.",
         ["Машиночитаемые схемы (DXF/PDF) или лист соответствий 'элемент схемы — подключение'."]),
        ("DOC_FORMAT",
         "Проверка оформления выполняется нейросетью по доступному тексту пояснительной записки и ведомостей.",
         ["Пояснительная записка/общие данные в машиночитаемом виде."]),
    ]:
        it = items[code]
        has_text = any((f.text_cache or "").strip() for f in files)
        if has_text:
            it.done([Finding.make(code, "info",
                                  "Проверка делегирована нейросетевому этапу (см. раздел LLM-анализ).")])
        else:
            it.not_performed(reason, need)

    return list(items.values())


# ======================= вспомогательные сборщики ============================
def _iter_tables(file_obj):
    """Генератор таблиц из text_cache: парсим повторно из файла невозможно —
    используем сериализованные таблицы, сохраненные в поле text_cache в виде
    JSON-секции (см. routes.upload) либо текстовое представление."""
    import json
    tc = file_obj.text_cache or ""
    marker = "\n@@TABLES_JSON@@\n"
    if marker in tc:
        _, js = tc.split(marker, 1)
        try:
            return json.loads(js)
        except Exception:
            return []
    return []


def _table_rows(file_obj):
    return [t.get("rows", []) for t in _iter_tables(file_obj)]


def _looks_like_cable_journal_table(rows):
    if not rows:
        return False
    head = norm_name(" ".join(str(x) for x in rows[0]))
    return ("кабел" in head and ("марк" in head or "сечен" in head or "наименован" in head)) \
        or "кабельный журнал" in head


def _collect_cable_journal(buckets):
    out = []
    for f in buckets["cable_journal"]:
        for tbl in _iter_tables(f):
            rows = tbl["rows"]
            if not rows:
                continue
            header = rows[0]
            ci_name = find_col(header, ["наименование", "марка", "марка кабеля", "кабель"])
            ci_len = find_col(header, ["длина", "length", "кол-во", "количество", "м, м"])
            ci_way = find_col(header, ["способ прокладки", "прокладка", "способ"])
            ci_mark = find_col(header, ["тип", "обозначение линии", "линия", "позиция"])
            if ci_name is None:
                continue
            for r in rows[1:]:
                name = str(r[ci_name]).strip() if ci_name < len(r) else ""
                if not name or not re.search(r"[А-Яа-я]{2,}", name):
                    continue
                length = to_num(r[ci_len]) if ci_len is not None and ci_len < len(r) else None
                way = str(r[ci_way]).strip().lower() if ci_way is not None and ci_way < len(r) else ""
                out.append({
                    "file": f.original_name, "name": name,
                    "name_norm": norm_name(re.split(r"[(,]", name)[0]),
                    "length": length, "way": way,
                    "mark": str(r[ci_mark]).strip() if ci_mark is not None and ci_mark < len(r) else "",
                })
    # агрегируем по нормализованному имени
    agg = {}
    for c in out:
        key = c["name_norm"]
        a = agg.setdefault(key, {"name": c["name"], "total_len": 0.0, "count": 0,
                                 "entries": [], "has_len": False})
        a["entries"].append(c)
        a["count"] += 1
        if c["length"]:
            a["total_len"] += c["length"]
            a["has_len"] = True
    return [{"name": v["name"], "name_norm": k, "total_len": round(v["total_len"], 2),
             "count": v["count"], "has_len": v["has_len"], "entries": v["entries"]}
            for k, v in agg.items()]


def _spec_tables(buckets):
    for f in buckets["specification"]:
        for tbl in _iter_tables(f):
            yield f, tbl


def _collect_spec_cables(buckets):
    out = []
    for f, tbl in _spec_tables(buckets):
        rows = tbl["rows"]
        if len(rows) < 2:
            continue
        header = rows[0]
        ci_name = find_col(header, ["наименование", "наименование материалов", "марка"])
        ci_qty = find_col(header, ["кол-во", "количество", "ед. изм", "м", "шт"])
        if ci_name is None:
            continue
        for r in rows[1:]:
            name = str(r[ci_name]).strip() if ci_name < len(r) else ""
            if not re.search(r"(кабел|КВВГ|ВВГ|ШВВП|UTP|FTP|нг|LS|HF)", name, re.I):
                continue
            qty = to_num(r[ci_qty]) if ci_qty is not None and ci_qty < len(r) else None
            out.append({"name": name, "qty": qty,
                        "name_norm": norm_name(re.split(r"[(,]", name)[0]),
                        "file": f.original_name})
    agg = {}
    for c in out:
        a = agg.setdefault(c["name_norm"], {"name": c["name"], "qty": 0.0, "found": False})
        if c["qty"]:
            a["qty"] += c["qty"]
            a["found"] = True
    return [{"name": v["name"], "name_norm": k, "qty": round(v["qty"], 2), "has_qty": v["found"]}
            for k, v in agg.items()]


def _collect_spec_equipment(buckets):
    names = []
    for f, tbl in _spec_tables(buckets):
        for r in tbl["rows"][1:]:
            for cell in r:
                s = str(cell).strip()
                if re.search(r"(датчик|извещатель|оповещатель|контроллер|панель|приемно|блок|клавиатура|считыватель|видеокамер|коммутатор|розетка|выключатель|автомат|щит|шкаф|ИБП|EPSI|РИП|С200|бокс)", s, re.I) and len(s) > 3:
                    names.append(s)
    return sorted(set(names))


def _collect_scheme_equipment(buckets):
    names = []
    for f in buckets["schemes"] + buckets["plans"]:
        txt = f.text_cache or ""
        for m in re.finditer(r"\b([А-ЯЁA-Z][А-ЯЁA-Z0-9\-]{1,}(?:-\d+|\d+))\b", txt):
            tok = m.group(1)
            if re.match(r"^(SA|SB|SC|SD|SE|KM|QF|QS|FU|TV|TA|TC|HL|HA|PK|PS|AP|GP|AT|MT|US|TV|PC|WP|EP|EL|EC|EF|FS|FA|FB|FC|FD|FE|FF|FG|FH|FI|FJ|FK|FL|FM|FN|FO)$", tok):
                continue
            if re.search(r"\d", tok) and len(tok) >= 2:
                names.append(tok)
        # явные перечни оборудования на листах схем
        for line in txt.splitlines():
            mm = re.match(r"^\s*(\S+)\s*[-–]\s*(.+)$", line)
            if mm and re.search(r"(извещател|датчик|камер|оповещател|контроллер|считывател|клавиатур)", mm.group(2), re.I):
                names.append(mm.group(1))
    uniq = sorted(set(n.upper() for n in names))
    return uniq


def _collect_trace_lengths(buckets):
    """Из планов (таблицы с колонкой 'длина трассы') собираем ведомость трасс."""
    traces = []
    for f in buckets["plans"]:
        for tbl in _iter_tables(f):
            rows = tbl["rows"]
            if len(rows) < 2:
                continue
            header = rows[0]
            ci_line = find_col(header, ["линия", "обозначение", "маркировка", "поз"])
            ci_len = find_col(header, ["длина трасс", "длина", "length"])
            ci_way = find_col(header, ["способ прокладки", "прокладка", "способ"])
            if ci_len is None:
                continue
            for r in rows[1:]:
                ln = to_num(r[ci_len]) if ci_len < len(r) else None
                if ln is None:
                    continue
                traces.append({
                    "file": f.original_name,
                    "line": str(r[ci_line]).strip() if ci_line is not None and ci_line < len(r) else "",
                    "length": ln,
                    "way": str(r[ci_way]).strip().lower() if ci_way is not None and ci_way < len(r) else "",
                })
    return traces


def _collect_loads(buckets):
    """Таблицы нагрузок: строки с током/мощностью и выбранным кабелем."""
    loads = []
    pool = buckets["loads"] + buckets["calculations"] + buckets["power"]
    for f in pool:
        for tbl in _iter_tables(f):
            rows = tbl["rows"]
            if len(rows) < 2:
                continue
            header = rows[0]
            ci_p = find_col(header, ["мощность", "power", "kw", "квт", "вт"])
            ci_i = find_col(header, ["ток", "current", "a,", "а,"])
            ci_u = find_col(header, ["напряжен", "kv", "в,"])
            ci_cos = find_col(header, ["cos", "косинус"])
            ci_cab = find_col(header, ["кабель", "марка кабеля", "сечение", "провод"])
            if ci_p is None and ci_i is None:
                continue
            for r in rows[1:]:
                p = to_num(r[ci_p]) if ci_p is not None and ci_p < len(r) else None
                i = to_num(r[ci_i]) if ci_i is not None and ci_i < len(r) else None
                cab = str(r[ci_cab]).strip() if ci_cab is not None and ci_cab < len(r) else ""
                if p is None and i is None:
                    continue
                u = to_num(r[ci_u]) if ci_u is not None and ci_u < len(r) else None
                loads.append({"file": f.original_name, "p_kw": p, "i_a": i,
                              "u_v": u, "cable": cab,
                              "row": " | ".join(str(x) for x in r)})
    return loads


_SEC_RE = re.compile(r"(?:x\s*)(\d+(?:[.,]\d+)?)\s*(?:мм|mm)?", re.I)
_CORE_RE = re.compile(r"(\d+)\s*x", re.I)


def _parse_cable_cross_section(cab):
    """Возвращает (сечение_жилы_мм2, кол-во_жил, материал) или (None,...)."""
    cores, sec = 1, None
    # Формат "число x число x сечение" — экранированный/витой кабель
    # (например КВВГЭнг-FRLS 1x2x0.78): жил 1 пара, сечение 0.78 мм2.
    m3 = re.search(r"(\d+)\s*[xх×]\s*(\d+)\s*[xх×]\s*(\d+(?:[.,]\d+)?)",
                   cab.replace(" ", ""))
    if m3:
        cores = int(m3.group(1)) * int(m3.group(2))
        sec = float(m3.group(3).replace(",", "."))
    else:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:мм|mm)?\s*(?:2|²)?\s*[xх×]\s*(\d+(?:[.,]\d+)?)",
                      cab.replace(" ", ""))
        if m:
            cores = int(float(m.group(1).replace(",", ".")))
            sec = float(m.group(2).replace(",", "."))
        else:
            m2 = re.search(r"[xх×](\d+(?:[.,]\d+)?)", cab.replace(" ", ""))
            if m2:
                sec = float(m2.group(1).replace(",", "."))
    al = bool(re.search(r"(^|[^\w])(асбл|аввг|аvvg|apshv|аc|al)(_|$|\d)", cab.lower())) or cab.lower().startswith("а")
    return sec, cores, ("Al" if al else "Cu")


def _check_cable_sizes(loads):
    findings = []
    checked = 0
    for ld in loads:
        cab = ld["cable"]
        if not cab:
            continue
        sec, cores, mat = _parse_cable_cross_section(cab)
        if sec is None:
            continue
        # приводим мощность к току
        i_a = ld["i_a"]
        if i_a is None and ld["p_kw"]:
            u = ld["u_v"] or 230.0
            if u <= 250:
                i_a = ld["p_kw"] * 1000.0 / (u * 0.9)   # cosφ≈0.9 по умолчанию НЕ принимаем
                findings.append(Finding.make(
                    "CABLE_SIZE", "minor",
                    f"В строке нагрузок '{ld['row']}' не указан cosφ/ток — расчет выполнен при cosφ=0.9 условно; требуется уточнение исходных данных.",
                    ntd_ref="ПУЭ изд.7 п.1.3.10"))
            else:
                i_a = ld["p_kw"] * 1000.0 / (1.732 * u * 0.9)
        if i_a is None:
            continue
        checked += 1
        table = CU_AMPACITY if mat == "Cu" else AL_AMPACITY
        allowed = table.get(sec) or table.get(round(sec * 2) / 2)
        if allowed is None:
            nearest = min(table.keys(), key=lambda k: abs(k - sec))
            allowed = table[nearest]
        if i_a > allowed:
            findings.append(Finding.make(
                "CABLE_SIZE", "critical",
                f"Кабель {cab}: расчетный ток {i_a:.1f} А превышает допустимый длительный ток {allowed} А для сечения {sec} мм² ({mat}). Требуется увеличение сечения.",
                ntd_ref="ПУЭ изд.7, табл. 1.3.6"))
    if checked == 0:
        findings.append(Finding.make(
            "CABLE_SIZE", "minor",
            "Формат таблиц нагрузок не позволяет однозначно связать нагрузку с выбранным кабелем — частичная проверка. Запросите у проектировщика сводную таблицу 'нагрузка — кабель'.",
            ntd_ref="СП 256.1325800.2016 (не входит в базу НТД системы)"))
    if not any(f["severity"] == "critical" for f in findings) and checked:
        findings.insert(0, Finding.make("CABLE_SIZE", "info",
                                        f"Проверено {checked} позиций 'нагрузка—кабель' по допустимому длительному току. Превышений не выявлено."))
    return findings


F_RATING_RE = re.compile(r"(?:IP|ip)\s*(\d{2})")
NG_REQUIRED_WORDS = ("здание", "помещен", "公共", "shaft", "лоток", "короб", "подвал", "этаж")


def _check_cable_marks(all_cables, buckets):
    findings = []
    plan_text = " ".join((f.text_cache or "") for f in buckets["plans"] + buckets["specification"])
    indoor = any(w in plan_text.lower() for w in NG_REQUIRED_WORDS)
    for name in all_cables:
        low = name.lower()
        is_power = bool(re.search(r"(ввг|nym|прод|асбл|аввг|пвв)", low))
        if indoor and is_power and "нг" not in low.replace("нг(а)", "").replace("нг(а)-", "нг"):
            pass  # проверяем точнее ниже
        if is_power and "нг" not in low:
            findings.append(Finding.make(
                "CABLE_MARK", "critical",
                f"Кабель '{name}' не имеет исполнения 'нг' (не распространяет горение). Для групповой прокладки в зданиях требуется исполнение нг(-LS/-HF).",
                ntd_ref="ГОСТ 31565-2012 п.5.2; СП 6.13130.2021 п.6.14.1"))
        if is_power and re.search(r"(нг-\w+-(plthh|hf|ls))", low) and "нг(a)" not in low and "нг(а)" not in low:
            findings.append(Finding.make(
                "CABLE_MARK", "minor",
                f"Кабель '{name}': при прокладке в пучках («по штропам/лоткам несколько кабелей») по ГОСТ 31565-2012 следует применять категории исполнения нг(А).",
                ntd_ref="ГОСТ 31565-2012 п.5.2, Таблица А"))
        if re.search(r"(витая пара|utp|ftp|sftp|cat[a-z0-9.]+)", low) and "нг" not in low:
            findings.append(Finding.make(
                "CABLE_MARK", "critical",
                f"Линия связи '{name}' без исполнения нг — для внутренней прокладки в зданиях кабель должен быть не распространяющим горение.",
                ntd_ref="ГОСТ 31565-2012; СП 6.13130.2021 п.6.14.1"))
        ipm = F_RATING_RE.search(name)
        if ipm and int(ipm.group(1)) < 54 and "улица" in plan_text.lower():
            findings.append(Finding.make("CABLE_MARK", "minor",
                                         f"Для уличной прокладки '{name}' проверьте степень защиты оболочки.",
                                         ntd_ref="ГОСТ Р 58238-2018"))
    if not findings:
        findings.append(Finding.make("CABLE_MARK", "info",
                                     "Явных нарушений по маркам кабелей в извлеченных данных не выявлено. Полная проверка условий прокладки — в LLM-блоке."))
    return findings


def _compare_cables(cj, spec):
    findings = []
    spec_map = {s["name_norm"]: s for s in spec}
    for c in cj:
        s = spec_map.get(c["name_norm"])
        if s is None:
            # ищем частичное совпадение
            cand = [x for x in spec if x["name_norm"].startswith(c["name_norm"][:6])]
            if not cand:
                findings.append(Finding.make(
                    "CJ_SPEC", "critical",
                    f"Кабель '{c['name']}' из кабельного журнала (суммарно {c['total_len']} м) отсутствует в спецификации.",
                    ntd_ref="ГОСТ Р 21.101-2026 п.6.5 (ведомости должны быть взаимоувязаны)"))
                continue
            s = cand[0]
        if s.get("has_qty") and c["has_len"]:
            diff = abs(s["qty"] - c["total_len"])
            tol = max(0.05 * s["qty"], 1.0)
            if diff > tol:
                findings.append(Finding.make(
                    "CJ_SPEC", "critical",
                    f"Расхождение количества кабеля '{c['name']}': кабельный журнал — {c['total_len']} м, спецификация — {s['qty']} м (разница {diff:.1f} м).",
                    ntd_ref="ГОСТ Р 21.101-2026 п.6.5; СП 76.13330.2016 п.6.16"))
            else:
                findings.append(Finding.make(
                    "CJ_SPEC", "info",
                    f"Кабель '{c['name']}': журнал {c['total_len']} м ≈ спецификация {s['qty']} м — расхождений нет."))
        elif not s.get("has_qty"):
            findings.append(Finding.make(
                "CJ_SPEC", "minor",
                f"В спецификации для '{c['name']}' не распознано количество — сверка выполнена частично.",
                ntd_ref="—"))
    for s in spec:
        if s["name_norm"] not in {c["name_norm"] for c in cj}:
            findings.append(Finding.make(
                "CJ_SPEC", "critical",
                f"Кабель '{s['name']}' указан в спецификации ({s['qty']} м), но отсутствует в кабельном журнале.",
                ntd_ref="ГОСТ Р 21.101-2026 п.6.5"))
    return findings


def _compare_equipment(scheme_names, spec_equip):
    findings = []
    spec_blob = norm_name(" ".join(spec_equip)).upper()
    missing = []
    for n in scheme_names:
        core = norm_name(n).upper()
        if len(core) < 2:
            continue
        if core not in spec_blob and not any(core in norm_name(x).upper() for x in spec_equip):
            missing.append(n)
    if missing:
        findings.append(Finding.make(
            "SCHEME_EQUIP", "critical",
            "Обозначения со схем не найдены в спецификации: " + ", ".join(missing[:30])
            + ("…" if len(missing) > 30 else ""),
            ntd_ref="ГОСТ Р 21.101-2026 п.6.5; ГОСТ 21.208-2013"))
    else:
        findings.append(Finding.make("SCHEME_EQUIP", "info",
                                     f"Проверено {len(scheme_names)} позиционных обозначений со схем — все присутствуют в спецификации."))
    return findings


def _compare_traces_vs_journal(traces, cj):
    findings = []
    total_by_line = {}
    for t in traces:
        key = norm_name(t["line"]) or f"#{len(total_by_line)}"
        coef = 1.0
        for w, c in LAYING_EXTRA.items():
            if w in (t["way"] or ""):
                coef = c
                break
        total_by_line.setdefault(key, {"len": 0.0, "way": t["way"], "coef": coef,
                                       "raw": t["length"]})
        total_by_line[key]["len"] += t["length"] * coef
    journal_total = sum(c["total_len"] for c in cj)
    plan_total = sum(v["len"] for v in total_by_line.values())
    if journal_total and plan_total:
        diff = plan_total - journal_total
        rel = abs(diff) / journal_total
        sev = "critical" if rel > 0.10 else ("minor" if rel > 0.03 else "info")
        findings.append(Finding.make(
            "TRACE_LEN", sev,
            f"Суммарная длина трасс с планов (с учетом коэффициентов способа прокладки) — {plan_total:.1f} м, "
            f"по кабельному журналу — {journal_total:.1f} м, расхождение {diff:+.1f} м ({rel * 100:.1f}%).",
            ntd_ref="СП 76.13330.2016 п.6.16; ГОСТ Р 21.101-2026 п.6.5"))
    return findings


def _collect_power_calcs(buckets):
    """Строки с тройками (мощность, ток, номинал защиты)."""
    out = []
    pool = buckets["power"] + buckets["calculations"] + buckets["loads"]
    for f in pool:
        for tbl in _iter_tables(f):
            rows = tbl["rows"]
            if len(rows) < 2:
                continue
            header = rows[0]
            ci_p = find_col(header, ["мощность", "kw", "квт"])
            ci_i = find_col(header, ["ток", "а,", " current"])
            ci_nom = find_col(header, ["номинал", "автомат", "защита", "уставк"])
            if ci_p is None or ci_i is None:
                continue
            for r in rows[1:]:
                p = to_num(r[ci_p]) if ci_p < len(r) else None
                i = to_num(r[ci_i]) if ci_i < len(r) else None
                nom = to_num(r[ci_nom]) if ci_nom is not None and ci_nom < len(r) else None
                if p and i:
                    out.append({"file": f.original_name, "p": p, "i": i, "nom": nom,
                                "row": " | ".join(str(x) for x in r)})
    return out


STANDARD_BREAKERS = [1, 2, 3, 4, 6, 10, 13, 16, 20, 25, 32, 40, 50, 63,
                     80, 100, 125, 160, 200, 250]


def _check_power_calcs(calcs):
    findings = []
    for c in calcs:
        p_w = c["p"] * 1000 if c["p"] < 100 else c["p"]  # кВт -> Вт (эвристика)
        i_calc = p_w / 230.0
        if c["i"] and abs(c["i"] - i_calc) / max(i_calc, 0.1) > 0.25:
            findings.append(Finding.make(
                "POWER_CALC", "minor",
                f"Строка '{c['row']}': заявленный ток {c['i']} А отличается от расчетного {i_calc:.1f} А (при 230 В) более чем на 25% — проверьте методiku пересчета (cosφ, коэффициент спроса, 3 фазы).",
                ntd_ref="ПУЭ изд.7 п.1.3.10; СП 256 (методика)"))
        if c["nom"] and c["i"]:
            if c["nom"] < c["i"]:
                findings.append(Finding.make(
                    "POWER_CALC", "critical",
                    f"Номинал защиты {c['nom']} А меньше расчетного тока {c['i']} А — ложные срабатывания/перегрев.",
                    ntd_ref="ПУЭ изд.7 п.3.1.4"))
            elif c["nom"] > 1.45 * c["i"]:
                findings.append(Finding.make(
                    "POWER_CALC", "minor",
                    f"Номинал защиты {c['nom']} А существенно (>1.45×) превышает расчетный ток {c['i']} А — проверьте селективность и защиту кабеля.",
                    ntd_ref="ПУЭ изд.7 п.1.3.10"))
            ok_std = min(STANDARD_BREAKERS, key=lambda x: abs(x - c["nom"]))
            if ok_std != c["nom"]:
                findings.append(Finding.make(
                    "POWER_CALC", "info",
                    f"Номинал {c['nom']} А не входит в стандартный ряд защитных аппаратов (ближайший {ok_std} А).",
                    ntd_ref="ГОСТ IEC 60898-1 (ряд токов)"))
    if not findings:
        findings.append(Finding.make("POWER_CALC", "info",
                                     f"Проверено {len(calcs)} строк расчета источников питания — грубых несоответствий не выявлено."))
    return findings


def _collect_battery_calcs(buckets):
    out = []
    pool = buckets["battery"] + buckets["power"] + buckets["calculations"]
    for f in pool:
        for tbl in _iter_tables(f):
            rows = tbl["rows"]
            if len(rows) < 2:
                continue
            header = rows[0]
            ci_i = find_col(header, ["ток разряда", "ток нагрузки", "ток, а", "ток"])
            ci_t = find_col(header, ["время", "автоном", "ч, ч", "час"])
            ci_c = find_col(header, ["емкость", "а·ч", "ач", "ah"])
            if ci_c is None and ci_i is None:
                continue
            for r in rows[1:]:
                out.append({
                    "file": f.original_name,
                    "i": to_num(r[ci_i]) if ci_i is not None and ci_i < len(r) else None,
                    "t": to_num(r[ci_t]) if ci_t is not None and ci_t < len(r) else None,
                    "cap": to_num(r[ci_c]) if ci_c < len(r) else None,
                    "row": " | ".join(str(x) for x in r),
                })
    return out


def _check_battery(calcs):
    """Грубая проверка: емкость >= ток × время × коэфф.запаса (0.7 глубина разряда)."""
    findings = []
    checked = 0
    for c in calcs:
        if not (c["i"] and c["t"] and c["cap"]):
            continue
        checked += 1
        need = c["i"] * c["t"] / 0.7  # DoD 70% для свинцово-кислых при 10-ч разряде
        if c["cap"] < need * 0.95:
            findings.append(Finding.make(
                "BATTERY", "critical",
                f"Емкость АКБ {c['cap']} А·ч недостаточна: при токе {c['i']} А и времени {c['t']} ч требуется ≥ {need:.1f} А·ч (с учетом глубины разряда 0.7).",
                ntd_ref="СП 6.13130.2021 п.5.5; пуэ-совместимая методика 10-часового разряда"))
        else:
            findings.append(Finding.make(
                "BATTERY", "info",
                f"АКБ {c['cap']} А·ч: обеспечено {c['cap'] * 0.7 / c['i']:.1f} ч при токе {c['i']} А — соответствует."))
    if checked == 0:
        findings.append(Finding.make(
            "BATTERY", "minor",
            "В найденных таблицах не удалось одновременно распознать ток, время автономии и емкость — проверка подбора АКБ выполнена частично. Запросите исходный расчет емкости.",
            ntd_ref="СП 6.13130.2021 п.5.5"))
    return findings
