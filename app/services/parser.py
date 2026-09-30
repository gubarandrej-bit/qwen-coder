# -*- coding: utf-8 -*-
"""Извлечение текста и таблиц из исходных файлов документации.

Поддерживаемые форматы входящей информации: xls/xlsx, doc/docx, pdf, dwg/dxf.

Ограничения (важно, ничего не выдумываем):
- .doc (старый бинарный Word) и .dwg (бинарный AutoCAD) не имеют надежного
  парсера на чистом Python. Для них возвращаем пустой текст с пометкой, и
  система сообщает пользователю, что содержимое файла не может быть
  проанализировано автоматически (нужна конвертация в .docx/.dxf или PDF).
- .dxf (текстовый обменный формат AutoCAD) разбирается эвристически:
  извлекаются текстовые строки (group code 1/7/300...).
"""
import os
import re


def extract_text(path: str, ext: str = None):
    """Возвращает (text, tables, note).

    text   - извлеченный текст;
    tables - список листов/таблиц: [{"name":..., "rows":[[...],[...]]}];
    note   - предупреждение, если извлечение неполное.
    """
    ext = (ext or os.path.splitext(path)[1]).lower().lstrip(".")
    try:
        if ext in ("xlsx", "xlsm"):
            return _extract_xlsx(path)
        if ext == "xls":
            return _extract_xls(path)
        if ext == "docx":
            return _extract_docx(path)
        if ext == "doc":
            return "", [], ("Формат .doc (бинарный Word 97-2003) не поддерживается "
                            "автоматическим извлечением. Загрузите файл в формате "
                            ".docx или PDF.")
        if ext == "pdf":
            return _extract_pdf(path)
        if ext == "dwg":
            return "", [], ("Формат .dwg (бинарный AutoCAD) не может быть разобран "
                            "без специализированного конвертера (ODA File Converter / "
                            "LibreDWG). Экспортируйте чертеж в .dxf или PDF и "
                            "загрузите повторно.")
        if ext == "dxf":
            return _extract_dxf(path)
        if ext in ("txt", "csv"):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(), [], ""
    except Exception as e:  # noqa: BLE001
        return "", [], f"Ошибка чтения файла: {e}"
    return "", [], f"Неизвестный формат .{ext}: содержимое не извлечено."


# ------------------------------------------------------------------ XLSX/XLS
def _extract_xlsx(path):
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True, read_only=True)
    chunks, tables = [], []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = ["" if c is None else str(c).strip() for c in row]
            if any(cells):
                rows.append(cells)
        tables.append({"name": ws.title, "rows": rows})
        chunks.append(f"=== ЛИСТ '{ws.title}' ===")
        for r in rows:
            chunks.append(" | ".join(r))
    wb.close()
    return "\n".join(chunks), tables, ""


def _extract_xls(path):
    # .xls (BIFF8) требует xlrd; если недоступен — честно сообщаем.
    try:
        import xlrd  # type: ignore
    except ImportError:
        return "", [], ("Для чтения формата .xls установите пакет 'xlrd' "
                        "(pip install xlrd) либо перезагрузите файл в формате .xlsx.")
    book = xlrd.open_workbook(path)
    chunks, tables = [], []
    for sh in book.sheets():
        rows = []
        for i in range(sh.nrows):
            cells = [str(c).strip() for c in sh.row_values(i)]
            if any(cells):
                rows.append(cells)
        tables.append({"name": sh.name, "rows": rows})
        chunks.append(f"=== ЛИСТ '{sh.name}' ===")
        for r in rows:
            chunks.append(" | ".join(r))
    return "\n".join(chunks), tables, ""


# ------------------------------------------------------------------ DOCX
def _extract_docx(path):
    from docx import Document
    doc = Document(path)
    chunks = []
    for p in doc.paragraphs:
        if p.text.strip():
            chunks.append(p.text.strip())
    tables = []
    for ti, t in enumerate(doc.tables):
        rows = []
        for row in t.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                rows.append(cells)
        tables.append({"name": f"Таблица {ti + 1}", "rows": rows})
        chunks.append(f"=== ТАБЛИЦА {ti + 1} ===")
        for r in rows:
            chunks.append(" | ".join(r))
    return "\n".join(chunks), tables, ""


# ------------------------------------------------------------------ PDF
def _extract_pdf(path):
    import pdfplumber
    chunks, tables = [], []
    with pdfplumber.open(path) as pdf:
        for pi, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            if txt.strip():
                chunks.append(f"=== СТРАНИЦА {pi + 1} ===\n{txt}")
            for ti, tb in enumerate(page.extract_tables() or []):
                rows = [["" if c is None else str(c).strip() for c in r] for r in tb]
                rows = [r for r in rows if any(r)]
                if rows:
                    name = f"PDF стр.{pi + 1} табл.{ti + 1}"
                    tables.append({"name": name, "rows": rows})
                    chunks.append(f"=== {name} ===")
                    for r in rows:
                        chunks.append(" | ".join(r))
    note = ""
    if not "".join(chunks).strip():
        note = ("В PDF не обнаружен машиночитаемый текст (возможно, скан). "
                "Требуется OCR — автоматический анализ содержимого не выполнен.")
    return "\n".join(chunks), tables, note


# ------------------------------------------------------------------ DXF
def _extract_dxf(path):
    """Эвристическое чтение DXF: извлечение текстовых строк чертежа."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    pairs = re.findall(r"^([A-Z0-9]{1,3})[\t ]*\n(.*)$", raw, re.M)
    texts = []
    for code, val in pairs:
        if code in ("1", "3", "7", "300", "301") and val.strip():
            texts.append(val.strip())
    note = ""
    if not texts:
        note = "В DXF не найдено текстовыхEntities — анализ содержимого ограничен."
    return "\n".join(texts), [], note
