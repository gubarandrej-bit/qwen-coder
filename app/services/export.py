# -*- coding: utf-8 -*-
"""Формирование выходных документов: отчеты (doc/xls), ведомости (xls),
ведомость замечаний, выгрузка результатов проверок."""
import io
from datetime import datetime

SEV_RU = {"critical": "Критическое", "minor": "Некритическое", "info": "Информация"}


# ============================================================ XLSX =========
def _xl_sheet(ws, title_rows, header, rows, widths=None):
    """Заполняет лист: заголовки сверху (опц.), затем шапка таблицы и данные."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    thin = Border(*[Side(style="thin")] * 4)
    row_i = 1
    for t in title_rows:
        ws.cell(row=row_i, column=1, value=t).font = Font(bold=True, size=12)
        row_i += 1
    if header:
        hr = row_i
        for j, h in enumerate(header, start=1):
            c = ws.cell(row=hr, column=j, value=h)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="2F3640")
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = thin
        data_start = hr + 1
    else:
        data_start = row_i
    for i, r in enumerate(rows, start=data_start):
        for j, v in enumerate(r, start=1):
            c = ws.cell(row=i, column=j, value=v)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = thin
    if widths:
        from openpyxl.utils import get_column_letter
        for j, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(j)].width = w
    return data_start + len(rows)


def report_xlsx(result, meta):
    """Отчет о проверке в XLSX: Лист1 — сведения/выводы, Замечания, Не проведено,
   Кабельный журнал vs Спецификация."""
    from openpyxl import Workbook
    wb = Workbook()

    ws = wb.active
    ws.title = "Отчет"
    info_rows = [
        ["Проект", meta.get("project", "")],
        ["Режим работы", meta.get("mode", "")],
        ["Модели ИИ", meta.get("models", "")],
        ["Дата формирования", datetime.now().strftime("%Y-%m-%d %H:%M")],
        ["Проверок выполнено", str(meta.get("performed", 0))],
        ["Проверок не выполнено", str(meta.get("not_performed", 0))],
        ["Критических замечаний", str(meta.get("critical", 0))],
        ["Некритических замечаний", str(meta.get("minor", 0))],
    ]
    _xl_sheet(ws, ["ОТЧЕТ О ПРОВЕРКЕ РАБОЧЕЙ ДОКУМЕНТАЦИИ ПО ИНЖЕНЕРНЫМ СИСТЕМАМ"], [], [])
    r = 2
    for k, v in info_rows:
        ws.cell(row=r, column=1, value=k).font = __import__("openpyxl").styles.Font(bold=True)
        ws.cell(row=r, column=2, value=v)
        r += 1
    ws.cell(row=r + 1, column=1, value="ВЫВОДЫ:")\
        .font = __import__("openpyxl").styles.Font(bold=True)
    ws.cell(row=r + 2, column=1, value=meta.get("summary", ""))

    ws2 = wb.create_sheet("Замечания")
    frows = []
    for f in result.get("findings", []):
        if f.get("severity") == "info":
            continue
        frows.append([f.get("check", ""), SEV_RU.get(f.get("severity"), ""),
                      f.get("message", ""), f.get("ntd_ref", "")])
    _xl_sheet(ws2, ["Перечень замечаний"],
              ["Проверка", "Критичность", "Суть замечания", "Основание (НТД)"],
              frows, widths=[14, 14, 90, 40])

    ws3 = wb.create_sheet("Не проведено")
    nrows = [[n.get("check", ""), n.get("reason", ""),
              "; ".join(n.get("data_needed", []) or [])]
             for n in result.get("not_checked", [])]
    _xl_sheet(ws3, ["Проверки, которые НЕ проводились"],
              ["Проверка", "Причина", "Запрос недостающих данных"],
              nrows, widths=[16, 70, 60])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def workbook_xlsx(sheets):
    """sheets: [(name, header, rows, widths)] → BytesIO xlsx."""
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, header, rows, widths in sheets:
        ws = wb.create_sheet(name[:31])
        _xl_sheet(ws, [], header, rows, widths)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def bomis_xlsx(bomis):
    """Ведомость объемов работ. bomis: list[dict(name, unit, qty, basis)]."""
    rows = [[i + 1, b.get("name", ""), b.get("unit", ""), b.get("qty", ""),
             b.get("basis", "")] for i, b in enumerate(bomis)]
    return workbook_xlsx([("ВОР",
                           ["№", "Наименование работ", "Ед. изм.", "Объем",
                            "Основание (чертеж/спецификация)"],
                           rows, [5, 70, 10, 12, 40])])


def spec_xlsx(items):
    """Ведомость оборудования и материалов. items: dict(name, unit, qty, note)."""
    rows = [[i + 1, it.get("name", ""), it.get("unit", ""), it.get("qty", ""),
             it.get("note", "")] for i, it in enumerate(items)]
    return workbook_xlsx([("Ведомость оборудования и материалов",
                           ["Поз.", "Наименование", "Ед. изм.", "Кол-во", "Примечание"],
                           rows, [6, 80, 10, 12, 30])])


def checks_history_xlsx(checks):
    rows = [[c.id, c.project_name, c.mode, c.status,
             str(c.started_at or ""), str(c.finished_at or ""),
             c.critical_count, c.minor_count] for c in checks]
    return workbook_xlsx([("Реестр проверок",
                           ["ID", "Проект", "Режим", "Статус", "Начало", "Окончание",
                            "Критич.", "Некритич."],
                           rows, [6, 40, 10, 10, 20, 20, 10, 10])])


def ntd_base_xlsx(docs):
    rows = [[d.code, d.title, d.doc_type, d.year or "", d.status,
             d.replaced_by, str(d.checked_at or ""), d.notes] for d in docs]
    return workbook_xlsx([("База НТД",
                           ["Обозначение", "Наименование", "Тип", "Год", "Статус",
                            "Заменен на", "Проверен", "Примечание"],
                           rows, [24, 70, 8, 8, 14, 20, 20, 30])])


# ============================================================ DOC ==========
def report_docx(result, meta):
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(11)

    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = t.add_run("ОТЧЕТ\nо проведении проверки проектной и рабочей документации "
                    "по инженерным системам")
    run.bold = True

    def kv(k, v):
        p = doc.add_paragraph()
        p.add_run(f"{k}: ").bold = True
        p.add_run(str(v))

    kv("Проект", meta.get("project", ""))
    kv("Режим работы системы", meta.get("mode", ""))
    kv("Модели ИИ", meta.get("models", ""))
    kv("Дата формирования", datetime.now().strftime("%Y-%m-%d %H:%M"))
    kv("Проверок выполнено / не выполнено",
       f"{meta.get('performed', 0)} / {meta.get('not_performed', 0)}")

    doc.add_heading("1. Перечень проведенных проверок", level=1)
    for c in result.get("checks", []):
        p = doc.add_paragraph(style="List Bullet")
        mark = "ВЫПОЛНЕНА" if c.get("performed") else "НЕ ВЫПОЛНЕНА"
        p.add_run(f"{c.get('title')} — {mark}")
        if not c.get("performed"):
            p.add_run(f". Причина: {c.get('reason')}")
            if c.get("data_needed"):
                p.add_run(" Запрашиваемые данные: " + "; ".join(c["data_needed"]))

    doc.add_heading("2. Критические замечания", level=1)
    crit = [f for f in result.get("findings", []) if f.get("severity") == "critical"]
    if not crit:
        doc.add_paragraph("Критических замечаний не выявлено.")
    for i, f in enumerate(crit, 1):
        p = doc.add_paragraph(f"{i}. {f.get('message')}")
        p.add_run(f"  Основание: {f.get('ntd_ref', '—')}").italic = True

    doc.add_heading("3. Некритические замечания", level=1)
    minor = [f for f in result.get("findings", []) if f.get("severity") == "minor"]
    if not minor:
        doc.add_paragraph("Некритических замечаний не выявлено.")
    for i, f in enumerate(minor, 1):
        p = doc.add_paragraph(f"{i}. {f.get('message')}")
        p.add_run(f"  Основание: {f.get('ntd_ref', '—')}").italic = True

    doc.add_heading("4. Проверки, которые не проводились", level=1)
    nc = result.get("not_checked", [])
    if not nc:
        doc.add_paragraph("Все запланированные проверки проведены.")
    for n in nc:
        doc.add_paragraph(f"[{n.get('check')}] {n.get('reason')}", style="List Bullet")

    doc.add_heading("5. Запрос недостающих исходных данных", level=1)
    dn = result.get("data_needed", [])
    if not dn:
        doc.add_paragraph("Недостающих данных нет / все необходимые данные предоставлены.")
    for d in sorted(set(dn)):
        doc.add_paragraph(d, style="List Bullet")

    doc.add_heading("6. Выводы", level=1)
    doc.add_paragraph(meta.get("summary", "") or
                      "Выводы сформированы по результатам перечисленных проверок.")
    doc.add_paragraph("Ответственность за подтверждение точных пунктов НТД при спорных "
                      "замечаниях остается за проектировщиком; система не дополняет "
                      "отсутствующие исходные данные домыслами.")

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf
