"""
Импорт PDF-отчёта «Касса» обратно в архив.

Разбирает PDF, который бот сам генерирует через /pdf (см. pdf_generator.py),
и восстанавливает из него day-dict в формате, совместимом с
sessions.overwrite_archive_day() — чтобы можно было вернуть в архив день,
если файл archive.json потерялся или испортился, из ранее сохранённого PDF.

Работает ТОЛЬКО со "своим" форматом PDF (тем, что рисует pdf_generator.py) —
это не универсальный OCR/парсер произвольных PDF.

См. PDF_IMPORT_PROGRESS.md — там описано архитектурное решение по восстановлению
зарплаты (через fixed_rates + price_breakdown с percent=0, а не пересчётом %).
"""
import re
from io import BytesIO

import pdfplumber

TABLE_SETTINGS = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}

# pdfplumber иногда режет таблицу машин на 8 колонок (внутренние vline создают
# "пустые" колонки на местах 2/4/6 — обычно когда на той же странице есть ещё и
# шапка-сводка), а иногда — чисто на 5 (на страницах-продолжениях без шапки).
# Поэтому колонки берём не по фиксированным индексам, а нормализуем каждую строку
# к 5 полям через _cells().
COL_NUM, COL_TXT, COL_SVC, COL_PRICE, COL_PAY = 0, 1, 2, 3, 4


def _cells(row: list) -> list:
    """Нормализует строку таблицы к 5 полям: [#, текст, услуга, цена, оплата]."""
    if len(row) == 5:
        return [c or "" for c in row]
    if len(row) == 8:
        return [row[0] or "", row[1] or "", row[3] or "", row[5] or "", row[7] or ""]
    # запасной вариант — убираем None и берём первые 5 непустых позиций по порядку
    non_none = [c for c in row if c is not None]
    if len(non_none) >= 5:
        return non_none[:5]
    return (non_none + [""] * 5)[:5]

_RE_MONEY   = re.compile(r"[\d\s]+")
_RE_ITOG    = re.compile(r"Итог\s+(.+?):\s*намыл\s+([\d\s]+)\s*руб\.\s*→\s*зарплата.*?=\s*([\d\s]+)\s*руб\.")
_RE_SAL_ROW = re.compile(r"([^—;]+?)\s*—\s*([\d\s]+)\s*руб\.")
_RE_DATE    = re.compile(r"Дата:\s*([\d.]+)")
_RE_LOYAL   = re.compile(r"Лояльность:\s*([\d\s]+)\s*руб\.")


class PdfImportError(ValueError):
    """PDF не распознан как отчёт «Касса» этого бота, или он повреждён/пуст."""


def _num(s: str) -> int:
    if not s:
        return 0
    digits = "".join(ch for ch in s if ch.isdigit())
    return int(digits) if digits else 0


def _clean(cell) -> str:
    return (cell or "").strip()


def _parse_payment(cell: str) -> tuple[str, dict | None]:
    """'нал' -> ('нал', None); 'нал 2000 / visa 1200' -> ('нал', {'нал':2000,'visa':1200})"""
    cell = _clean(cell)
    if "/" in cell:
        split = {}
        for part in cell.split("/"):
            part = part.strip()
            m = re.match(r"([A-Za-zА-Яа-яЁё]+)\s+([\d\s]+)", part)
            if m:
                split[m.group(1)] = _num(m.group(2))
        if split:
            return next(iter(split)), split
    return cell, None


def _parse_header_block(rows: list[list]) -> dict:
    """Парсит строки шапки-таблицы (первая таблица на 1-й странице) — дата,
    зарплаты по всем (мойщики+админ последним), расходы, доходы, лояльность."""
    info = {
        "date": None, "washer_targets": {}, "admin_name": None, "admin_salary": None,
        "expenses": [], "incomes": [], "total_loyalty": 0,
    }
    for row in rows:
        first = _clean(_cells(row)[COL_NUM])
        if first.startswith("Дата:"):
            m = _RE_DATE.search(first)
            if m:
                info["date"] = m.group(1)
        elif first.startswith("Лояльность:"):
            m = _RE_LOYAL.search(first)
            if m:
                info["total_loyalty"] = _num(m.group(1))
        elif first.startswith("Зарплата:"):
            # Строка "Зарплата:" не использует правую колонку "Итого" под
            # значение — если там что-то есть, это перенос текста, вышедшего
            # за пределы левой ячейки (много сотрудников -> длинная строка),
            # а не отдельное число. Приклеиваем обратно.
            overflow = "".join(_clean(c) for c in row if c is not None)[len(first):]
            body = (first + overflow)[len("Зарплата:"):]
            pairs = _RE_SAL_ROW.findall(body)
            names = [(n.strip(), _num(a)) for n, a in pairs]
            if names:
                *washers, admin = names
                for name, amount in washers:
                    info["washer_targets"][name] = amount
                info["admin_name"], info["admin_salary"] = admin
        elif first.startswith("Расходы:"):
            body = first[len("Расходы:"):].strip()
            if body and body != "нет":
                for part in body.split(";"):
                    part = part.strip()
                    if not part:
                        continue
                    if " - " in part:
                        name, amount = part.rsplit(" - ", 1)
                        info["expenses"].append({"name": name.strip(), "amount": _num(amount)})
        elif first.startswith("Доходы:"):
            body = first[len("Доходы:"):].strip()
            if body and body != "нет":
                for part in body.split(";"):
                    part = part.strip()
                    if not part:
                        continue
                    if " - " in part:
                        name, amount = part.rsplit(" - ", 1)
                        info["incomes"].append({"name": name.strip(), "amount": _num(amount)})
    return info


def _is_car_table_header(row: list) -> bool:
    return _clean(_cells(row)[COL_NUM]) == "#"


def _parse_car_table(all_rows: list[list], washer_targets: dict) -> tuple[list, list, dict]:
    """Парсит строки таблицы машин (может идти по нескольким страницам).
    Возвращает (cars, products, washer_actuals) где washer_actuals — {emp: (namyl, salary)}
    из зелёных итоговых строк (для сверки/восстановления)."""
    cars, products = [], []
    current_employee = None
    mode = "cars"  # "cars" | "products"
    washer_actuals = {}
    car_num = 0

    for row in all_rows:
        num_c, txt_c, svc_c, price_c, pay_c = (_clean(c) for c in _cells(row))

        if not txt_c and not svc_c and not price_c and not pay_c:
            continue  # пустая строка-заполнитель

        if txt_c.startswith("Итог "):
            full = txt_c + " " + svc_c  # текст мог разорваться на 2 колонки
            m = _RE_ITOG.search(full)
            if m:
                washer_actuals[m.group(1).strip()] = (_num(m.group(2)), _num(m.group(3)))
            continue

        if txt_c.startswith("Товары (итого"):
            mode = "products"
            current_employee = None
            continue

        if not num_c and txt_c and not price_c and not pay_c:
            # синяя строка с именем сотрудника
            current_employee = txt_c
            mode = "cars"
            continue

        if svc_c == "Товар" or (mode == "products" and price_c and pay_c):
            payment, split = _parse_payment(pay_c)
            products.append({
                "name": txt_c,
                "price": _num(price_c),
                "payment": payment,
            })
            continue

        if num_c.isdigit() and txt_c:
            car_num += 1
            payment, split = _parse_payment(pay_c)
            price = _num(price_c)
            car = {
                "num": car_num,
                "car": txt_c,
                "employee": current_employee or "?",
                "service": svc_c,
                "price": price,
                "payment": payment,
                # percent=0 — зарплата мойщика восстанавливается целиком через
                # fixed_rates (см. PDF_IMPORT_PROGRESS.md), а не через % услуги,
                # который не восстановить однозначно по текстовой метке из PDF.
                "price_breakdown": {"_imported": {"name": svc_c or "Импорт из PDF", "price": price, "percent": 0}},
            }
            if split:
                car["payment_split"] = split
            cars.append(car)
            continue
        # иначе — необрабатываемая строка (например, обрывок шапки), пропускаем

    return cars, products, washer_actuals


def parse_kassa_pdf(file_bytes: bytes) -> dict:
    """Главная функция. Возвращает day-dict, готовый для
    sessions.overwrite_archive_day(branch, date, day), плюс служебные поля
    '_warnings' (список предупреждений для показа пользователю) и
    '_admin_name_hint' (для подбора филиала).

    Бросает PdfImportError, если PDF не похож на отчёт «Касса» этого бота.
    """
    warnings = []
    try:
        pdf = pdfplumber.open(BytesIO(file_bytes))
    except Exception as e:
        raise PdfImportError(f"Не удалось открыть PDF: {e}")

    with pdf:
        if not pdf.pages:
            raise PdfImportError("Пустой PDF.")

        # ReportLab рисует шапку-сводку и таблицу машин одной непрерывной сеткой
        # линий, поэтому pdfplumber на 1-й странице обычно отдаёт их как ОДНУ
        # таблицу — делим её сами по первой строке-заголовку "#".
        all_rows = []
        for page in pdf.pages:
            for table in page.extract_tables(TABLE_SETTINGS):
                all_rows.extend(table)

        split_idx = next((i for i, r in enumerate(all_rows) if _is_car_table_header(r)), None)
        if split_idx is None:
            raise PdfImportError(
                "Не похоже на отчёт «Касса» этого бота — не нашёл таблицу машин. "
                "Импорт работает только с PDF, которые бот сам сгенерировал по /pdf."
            )
        header_rows = all_rows[:split_idx]
        all_table_rows = all_rows[split_idx:]
        header_info = _parse_header_block(header_rows)

        if not header_info.get("date"):
            raise PdfImportError(
                "Не похоже на отчёт «Касса» этого бота — не нашёл строку «Дата:». "
                "Импорт работает только с PDF, которые бот сам сгенерировал по /pdf."
            )

        # Убираем строку-заголовок таблицы машин ('#','Марка...',...) — она может
        # повторяться на каждой странице.
        car_rows = [r for r in all_table_rows if not _is_car_table_header(r)]
        cars, products, washer_actuals = _parse_car_table(car_rows, header_info["washer_targets"])

        if not cars and not products:
            raise PdfImportError("В PDF не нашлось ни одной машины/товара для импорта.")

        # ── Восстановление зарплат мойщиков через fixed_rates ──
        fixed_rates = {}
        for emp, target in header_info["washer_targets"].items():
            fixed_rates[emp] = target
        # Сверка "намыл" из зелёных строк с суммой перенесённых цен — просто предупреждение,
        # сами данные это не портит.
        for emp, (namyl, salary) in washer_actuals.items():
            actual_sum = sum(c["price"] for c in cars if c["employee"] == emp)
            if actual_sum != namyl:
                warnings.append(
                    f"⚠️ {emp}: сумма перенесённых машин ({actual_sum}₽) не совпала с "
                    f"«намыл» в PDF ({namyl}₽) — возможно, часть строк не распозналась.")

        admin_name = header_info["admin_name"] or ""
        admin_salary = header_info["admin_salary"] or 0

        # ── Лояльность: лучшее приближение (см. PDF_IMPORT_PROGRESS.md) ──
        loyalty = []
        total_loyalty = header_info["total_loyalty"]
        if total_loyalty:
            target_car = next((c for c in cars if c["payment"] in ("нал", "наличка")), None) or (cars[0] if cars else None)
            if target_car:
                loyalty.append({"car_num": target_car["num"], "discount": total_loyalty})
                warnings.append(
                    f"⚠️ В PDF есть скидка лояльности {total_loyalty}₽ — она привязана к "
                    f"первой подходящей машине приближённо, точная разбивка по оплате "
                    f"могла отличаться от оригинала (сама выручка и зарплаты — нет)."
                )

        day = {
            "date":             header_info["date"],
            "branch":           None,  # определяется вызывающим кодом (bot.py)
            "cars":             cars,
            "products":         products,
            "expenses":         header_info["expenses"],
            "incomes":          header_info["incomes"],
            "loyalty":          loyalty,
            "admin_percent":    0,
            "admin_name":       admin_name,
            "fixed_rates":      fixed_rates,
            "admin_fixed_rate": admin_salary,
            "_warnings":        warnings,
            "_admin_name_hint": admin_name,
        }
        return day
