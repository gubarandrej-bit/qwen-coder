# -*- coding: utf-8 -*-
"""Unit-тесты расчётных модулей системы DocCheck (без БД и сети).

Проверяют чистые функции app/services/checks.py:
 - парсинг сечения/жил/материала кабеля;
 - проверка сечения кабеля по допустимому длительному току (ПУЭ табл. 1.3.6);
 - сверка кабельного журнала со спецификацией (ГОСТ Р 21.101-2026);
 - проверка расчетов источников питания (ток, номинал защиты);
 - проверка подбора АКБ (СП 6.13130.2021);
 - проверка марок кабелей по пожарной безопасности (ГОСТ 31565-2012);
 - утилиты to_num / norm_name / find_col / parse_table.

Запуск:  python scripts/unit_tests.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.checks import (  # noqa: E402
    to_num, norm_name, find_col, parse_table,
    _parse_cable_cross_section, _check_cable_sizes, _compare_cables,
    _check_power_calcs, _check_battery, _check_cable_marks,
)

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  [OK]   {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}  {detail}")


class FakeFile:
    """Миниатюрная заглушка загруженного файла для _check_cable_marks."""
    def __init__(self, text=""):
        self.text_cache = text


print("== Утилиты ==")
check("to_num('1 234,5')", to_num("1 234,5") == 1234.5, str(to_num("1 234,5")))
check("to_num('abc') -> None", to_num("abc") is None)
check("norm_name регистр/пробелы", norm_name("  ВВГ  НГ  LS ") == norm_name("ввг нг ls"))
check("find_col по ключевым словам", find_col(["№", "Наименование", "Кол-во"], ["кол-во", "количество"]) == 2)
hdr, rows = parse_table({"rows": [["A", "B"], ["1", "2"], ["3", "4"]]})
check("parse_table возвращает header+rows", hdr == ["A", "B"] and len(rows) == 2)

print("== Парсинг сечения кабеля ==")
sec, cores, mat = _parse_cable_cross_section("ВВГнг(А)-LS 3х2,5")
check("3х2,5 -> сечение 2.5, жил 3, Cu", sec == 2.5 and cores == 3 and mat == "Cu",
      f"{sec},{cores},{mat}")
sec, cores, mat = _parse_cable_cross_section("АВВГ 4х16")
check("АВВГ 4х16 -> Al, 16 мм2", sec == 16.0 and mat == "Al", f"{sec},{cores},{mat}")
sec, _, _ = _parse_cable_cross_section("КВВГЭнг(А)-FRLS 1x2x0.78")
check("1x2x0.78 -> 0.78", sec == 0.78, str(sec))
sec, _, _ = _parse_cable_cross_section("УТП cat5e")
check("без сечения -> None", sec is None)

print("== Проверка сечения по нагрузке (ПУЭ табл.1.3.6) ==")
# 25 А на 1.5 мм2 Cu (доп. 23 А) — критическое замечание
f = _check_cable_sizes([{"cable": "ВВГнг-LS 3х1.5", "i_a": 25.0, "p_kw": None,
                         "u_v": None, "row": "r1"}])
crit = [x for x in f if x["severity"] == "critical"]
check("завышенный ток -> critical CABLE_SIZE", len(crit) == 1 and "превышает" in crit[0]["message"],
      str(f))
check("ссылка на ПУЭ табл.1.3.6", crit and "ПУЭ" in crit[0]["ntd_ref"])
# 10 А на 2.5 мм2 Cu (доп. 30 А) — нарушений нет, есть info
f2 = _check_cable_sizes([{"cable": "ВВГнг-LS 3х2.5", "i_a": 10.0, "p_kw": None,
                          "u_v": None, "row": "r2"}])
check("нормальное сечение -> info без critical",
      not any(x["severity"] == "critical" for x in f2) and any(x["severity"] == "info" for x in f2),
      str(f2))
# Пересчет из мощности: 5 кВт, 230 В, cosφ указан как отсутствие -> minor + расчет
f3 = _check_cable_sizes([{"cable": "NYM 3х1.5", "i_a": None, "p_kw": 5.0,
                          "u_v": 230.0, "row": "r3"}])
check("5 кВт на 1.5 мм2 -> critical (~24 А > 23 А)",
      any(x["severity"] == "critical" for x in f3), str(f3))

print("== Сверка кабельного журнала со спецификацией ==")
cj = [{"name": "ВВГнг(А)-LS 3х2.5", "name_norm": norm_name("ВВГнг-LS 3х2.5"),
       "total_len": 100.0, "has_len": True}]
spec_ok = [{"name": "ВВГнг(А)-LS 3х2.5", "name_norm": norm_name("ВВГнг-LS 3х2.5"),
            "qty": 102.0, "has_qty": True}]
f = _compare_cables(cj, spec_ok)
check("журнал ≈ спецификация (±5%) -> info, без critical",
      not any(x["severity"] == "critical" for x in f), str(f))
spec_bad = [{"name": "ВВГнг(А)-LS 3х2.5", "name_norm": norm_name("ВВГнг(А)-LS 3х2.5"),
             "qty": 80.0, "has_qty": True}]
f = _compare_cables(cj, spec_bad)
crit = [x for x in f if x["severity"] == "critical"]
check("расхождение 100 vs 80 м -> critical", len(crit) == 1 and "Расхождение" in crit[0]["message"])
check("критич. замечание ссылается на ГОСТ Р 21.101-2026",
      crit and "21.101" in crit[0]["ntd_ref"])
f = _compare_cables(cj, [])
check("кабель КЖ отсутствует в спецификации -> critical",
      any(x["severity"] == "critical" and "отсутствует в спецификации" in x["message"] for x in f))
f = _compare_cables([], spec_ok)
check("кабель спецификации отсутствует в КЖ -> critical",
      any(x["severity"] == "critical" and "отсутствует в кабельном журнале" in x["message"] for x in f))

print("== Расчеты источников питания ==")
f = _check_power_calcs([{"p": 5.0, "i": 21.7, "nom": 16, "row": "r"}])
check("номинал 16 А < ток 21.7 А -> critical",
      any(x["severity"] == "critical" and "меньше расчетного" in x["message"] for x in f), str(f))
f = _check_power_calcs([{"p": 3.0, "i": 13.0, "nom": 16, "row": "r"}])
check("16 А при 13 А -> без critical", not any(x["severity"] == "critical" for x in f))
f = _check_power_calcs([{"p": 3.0, "i": 13.0, "nom": 40, "row": "r"}])
check("40 А при 13 А (>1.45x) -> minor",
      any(x["severity"] == "minor" for x in f), str(f))
f = _check_power_calcs([{"p": 1.0, "i": 4.3, "nom": 15, "row": "r"}])
check("нестандартный номинал 15 А -> info про ряд",
      any(x["severity"] == "info" and "стандартн" in x["message"] for x in f), str(f))
f = _check_power_calcs([{"p": 5.0, "i": 100.0, "nom": None, "row": "r"}])
check("заявленный ток расходится >25% -> minor методика",
      any(x["severity"] == "minor" and "отличается от расчетного" in x["message"] for x in f), str(f))

print("== Подбор АКБ ==")
# ИП 200 Вт = 24 В => ~8.3 А, 24 ч автономии => нужно ~285 А·ч; ставим 17 А·ч
f = _check_battery([{"i": 8.3, "t": 24.0, "cap": 17.0, "row": "r"}])
check("17 А·ч при 8.3 А/24 ч -> critical",
      any(x["severity"] == "critical" and "недостаточна" in x["message"] for x in f), str(f))
f = _check_battery([{"i": 2.0, "t": 3.0, "cap": 55.0, "row": "r"}])
check("55 А·ч при 2 А/3 ч -> info соответствует",
      any(x["severity"] == "info" and "соответствует" in x["message"] for x in f), str(f))
f = _check_battery([{"i": None, "t": 3.0, "cap": 55.0, "row": "r"}])
check("нет данных тока -> minor частичная проверка (ничего не выдумываем)",
      any(x["severity"] == "minor" for x in f), str(f))

print("== Марки кабелей (ГОСТ 31565-2012) ==")
buckets = {"plans": [FakeFile("план этажа здания")], "specification": [],
           "cable_journal": [], "loads": [], "power": [], "calculations": [],
           "battery": [], "schemes": [], "other": []}
f = _check_cable_marks(["ВВГ 3х2.5"], buckets)
check("ВВГ без 'нг' в здании -> critical",
      any(x["severity"] == "critical" and "нг" in x["message"] for x in f), str(f))
f = _check_cable_marks(["ВВГнг(А)-LS 3х2.5"], buckets)
check("ВВГнг(А)-LS -> без critical",
      not any(x["severity"] == "critical" for x in f), str(f))
f = _check_cable_marks(["Кабель витая пара UTP cat5e"], buckets)
check("UTP без нг -> critical",
      any(x["severity"] == "critical" for x in f), str(f))

print()
total = len(PASSED) + len(FAILED)
print(f"ИТОГО unit-тестов: прошло {len(PASSED)}, провалилось {len(FAILED)} (из {total})")
if FAILED:
    for n in FAILED:
        print("  FAIL:", n)
    sys.exit(1)
print("UNIT TESTS: ALL OK")
