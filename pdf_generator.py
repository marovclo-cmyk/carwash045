from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
import os, sys
from sessions import get_branch_admin_name

def _register_fonts():
    """Регистрируем шрифты с поддержкой кириллицы для Windows/Linux/Mac."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    bundled = {
        "Times":      os.path.join(base_dir, "assets", "fonts", "GoshaSans-Regular.ttf"),
        "Times-Bold": os.path.join(base_dir, "assets", "fonts", "GoshaSans-Bold.ttf"),
    }

    # Пути к системным шрифтам на разных ОС (запасной вариант, если вдруг нет assets/fonts)
    candidates = {
        "Times": [],
        "Times-Bold": [],
    }

    if sys.platform == "win32":
        winfonts = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "Fonts")
        candidates["Times"]      = [
            os.path.join(winfonts, "times.ttf"),
            os.path.join(winfonts, "arial.ttf"),
        ]
        candidates["Times-Bold"] = [
            os.path.join(winfonts, "timesbd.ttf"),
            os.path.join(winfonts, "arialbd.ttf"),
        ]
    elif sys.platform == "darwin":  # macOS
        candidates["Times"]      = ["/Library/Fonts/Times New Roman.ttf"]
        candidates["Times-Bold"] = ["/Library/Fonts/Times New Roman Bold.ttf"]
    else:  # Linux
        candidates["Times"]      = [
            "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        ]
        candidates["Times-Bold"] = [
            "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        ]

    for font_name in candidates:
        paths = [bundled[font_name]] + candidates[font_name]
        registered = False
        for path in paths:
            if os.path.exists(path):
                pdfmetrics.registerFont(TTFont(font_name, path))
                registered = True
                break
        if not registered:
            # Крайний фолбэк: встроенный шрифт ReportLab (без кириллицы, но не упадёт)
            fallback = "Helvetica-Bold" if "Bold" in font_name else "Helvetica"
            pdfmetrics.getFont(fallback)
            pdfmetrics._fonts[font_name] = pdfmetrics._fonts[fallback]

_register_fonts()

PAGE_W, PAGE_H = A4
ML = 20 * mm   # левое поле
MR = 15 * mm   # правое поле
MT = 15 * mm   # верхнее поле
MB = 15 * mm   # нижнее поле
CW = PAGE_W - ML - MR   # ширина контента

F  = "Times"
FB = "Times-Bold"
FS = 10
ROW_H  = 6 * mm    # строки таблицы машин
HEAD_H = 6.5 * mm  # строки шапки

BLACK = colors.black
GREY  = colors.HexColor("#CCCCCC")  # не используется, сетка всегда чёрная


# ─────────────────────────────────────────────────────────────────────────────
# Базовые примитивы
# ─────────────────────────────────────────────────────────────────────────────

def rect(c, x, y_top, w, h):
    """Рисует прямоугольник (y_top — верхний край)."""
    c.setStrokeColor(BLACK)
    c.setLineWidth(0.5)
    c.rect(x, y_top - h, w, h, fill=0, stroke=1)


def filled_rect(c, x, y_top, w, h, fill_color):
    c.setFillColor(fill_color)
    c.rect(x, y_top - h, w, h, fill=1, stroke=0)
    c.setFillColor(BLACK)


def text_in(c, x, y_top, w, h, txt, font=F, size=FS, align="left", bold=False, color=BLACK):
    """Текст внутри ячейки с отступами."""
    if not txt:
        return
    c.setFont(FB if bold else font, size)
    c.setFillColor(color)
    pad = 2 * mm
    ty  = y_top - h * 0.62   # вертикальный центр
    if align == "center":
        c.drawCentredString(x + w / 2, ty, str(txt))
    elif align == "right":
        c.drawRightString(x + w - pad, ty, str(txt))
    else:
        c.drawString(x + pad, ty, str(txt))
    c.setFillColor(BLACK)


def hline(c, x, y, w, lw=0.5):
    c.setStrokeColor(BLACK)
    c.setLineWidth(lw)
    c.line(x, y, x + w, y)


def vline(c, x, y_top, h, lw=0.5):
    c.setStrokeColor(BLACK)
    c.setLineWidth(lw)
    c.line(x, y_top, x, y_top - h)


# ─────────────────────────────────────────────────────────────────────────────
# Колонки таблицы машин
# ─────────────────────────────────────────────────────────────────────────────

def cols():
    num   = 9  * mm
    price = 26 * mm
    pay   = 25 * mm
    svc   = 38 * mm
    car   = CW - num - price - pay - svc
    return [num, car, svc, price, pay]


# ─────────────────────────────────────────────────────────────────────────────
# ШАПКА — таблица как на бланке
# ─────────────────────────────────────────────────────────────────────────────

def draw_header_table(c, session, summary, y_top):
    x  = ML
    w  = CW
    rh = HEAD_H   # высота строки шапки
    right_col = 32 * mm   # ширина правой колонки "Итого"

    def header_row(label, value_left="", value_right="", bold_label=True, y=None):
        nonlocal y_top
        if y is None:
            y = y_top

        # Внешний прямоугольник строки
        rect(c, x, y, w, rh)

        # Вертикальный разделитель перед "Итого"
        vline(c, x + w - right_col, y, rh)

        # Левая ячейка — метка
        text_in(c, x, y, w - right_col, rh, label, bold=bold_label, size=FS)

        # Правая ячейка — значение
        if value_right:
            text_in(c, x + w - right_col, y, right_col, rh,
                    value_right, bold=True, align="center", size=FS)

        y_top -= rh
        return y_top

    # ── Строка 1: Дата
    # Внешний прямоугольник
    rect(c, x, y_top, w, rh)
    vline(c, x + w - right_col, y_top, rh)
    text_in(c, x, y_top, w - right_col, rh, f"Дата:   {session['date']}", bold=True, size=FS)
    y_top -= rh

    # ── Строка 2: Сотрудники + Итого
    rect(c, x, y_top, w, rh)
    vline(c, x + w - right_col, y_top, rh)
    # "Итого" — заголовок колонки
    text_in(c, x, y_top, w - right_col, rh, "Сотрудники:", bold=True, size=FS)
    text_in(c, x + w - right_col, y_top, right_col, rh, "Итого", bold=True, align="center", size=FS)
    y_top -= rh

    # ── Строка 3: Дневная выручка
    rect(c, x, y_top, w, rh)
    vline(c, x + w - right_col, y_top, rh)
    text_in(c, x, y_top, w - right_col, rh, "Дневная выручка (общая):", bold=True, size=FS)
    grand = summary.get("grand_total", summary["total"])
    text_in(c, x + w - right_col, y_top, right_col, rh,
            f"{grand} руб.", bold=True, align="center", size=FS)
    y_top -= rh

    # ── Строка 3.1: Лояльность (только если есть) — справочно, не входит в кассу
    total_loyalty = summary.get("total_loyalty", 0)
    if total_loyalty > 0:
        rect(c, x, y_top, w, rh)
        vline(c, x + w - right_col, y_top, rh)
        text_in(c, x, y_top, w - right_col, rh, f"Лояльность: {total_loyalty} руб.", bold=False, size=FS)
        y_top -= rh

    # ── Строка 4: Наличка / Безнал / Visa
    rect(c, x, y_top, w, rh)
    vline(c, x + w - right_col, y_top, rh)

    # Три подколонки внутри левой части
    lw = w - right_col
    third = lw / 3
    vline(c, x + third,       y_top, rh)
    vline(c, x + third * 2,   y_top, rh)

    text_in(c, x,                y_top, third, rh, f"Наличка:  {summary['cash']} руб.",   bold=False, size=FS)
    text_in(c, x + third,        y_top, third, rh, f"Безнал:  {summary['beznal']} руб.",  bold=False, size=FS)
    text_in(c, x + third * 2,    y_top, third, rh, f"Visa:  {summary['visa']} руб.",      bold=False, size=FS)
    y_top -= rh

    # ── Строка 5: Зарплата
    rect(c, x, y_top, w, rh)
    vline(c, x + w - right_col, y_top, rh)
    admin_name = get_branch_admin_name(session.get("branch", ""))
    sal_parts = [f"{e} — {s} руб." for e, s in summary["washer_salaries"].items()]
    sal_parts.append(f"{admin_name} — {summary['admin_salary']} руб.")
    sal_str = ";   ".join(sal_parts)
    text_in(c, x, y_top, 18*mm, rh, "Зарплата:", bold=True, size=FS)
    text_in(c, x + 18*mm, y_top, w - right_col - 18*mm, rh, sal_str, bold=False, size=FS - 0.5)
    y_top -= rh

    # ── Строка 6: Расходы
    rect(c, x, y_top, w, rh)
    vline(c, x + w - right_col, y_top, rh)
    exp_str = summary["expenses_str"] if summary["total_expenses"] > 0 else "нет"
    text_in(c, x, y_top, 18*mm, rh, "Расходы:", bold=True, size=FS)
    text_in(c, x + 18*mm, y_top, w - right_col - 18*mm, rh, exp_str, bold=False, size=FS)
    if summary["total_expenses"] > 0:
        text_in(c, x + w - right_col, y_top, right_col, rh,
                f"{summary['total_expenses']} руб.", bold=False, align="center", size=FS)
    y_top -= rh

    # ── Строка 6.5: Доходы (если есть)
    if summary.get("total_incomes"):
        rect(c, x, y_top, w, rh)
        vline(c, x + w - right_col, y_top, rh)
        text_in(c, x, y_top, 18*mm, rh, "Доходы:", bold=True, size=FS)
        text_in(c, x + 18*mm, y_top, w - right_col - 18*mm, rh, summary["incomes_str"], bold=False, size=FS)
        text_in(c, x + w - right_col, y_top, right_col, rh,
                f"{summary['total_incomes']} руб.", bold=False, align="center", size=FS)
        y_top -= rh

    # ── Строка 7: Остаток
    rect(c, x, y_top, w, rh)
    vline(c, x + w - right_col, y_top, rh)
    text_in(c, x, y_top, 18*mm, rh, "Остаток:", bold=True, size=FS)
    text_in(c, x + w - right_col, y_top, right_col, rh,
            f"{summary['remainder']} руб.", bold=True, align="center", size=FS + 1)
    y_top -= rh

    return y_top


# ─────────────────────────────────────────────────────────────────────────────
# Заголовок таблицы машин
# ─────────────────────────────────────────────────────────────────────────────

def draw_table_header(c, y_top):
    x  = ML
    ws = cols()
    h  = ROW_H

    # Фон заголовка
    filled_rect(c, x, y_top, CW, h, colors.HexColor("#1F3864"))

    # Внешний контур + вертикальные линии
    cx = x
    labels    = ["#", "Марка автомобиля (Клиент)", "Вид услуги", "Стоимость", "Вид оплаты"]
    aligns    = ["center", "left", "center", "center", "center"]
    for i, (w, lbl, aln) in enumerate(zip(ws, labels, aligns)):
        rect(c, cx, y_top, w, h)
        text_in(c, cx, y_top, w, h, lbl, bold=True, align=aln,
                size=FS - 0.5, color=colors.white)
        cx += w

    return y_top - h


# ─────────────────────────────────────────────────────────────────────────────
# Строки таблицы машин
# ─────────────────────────────────────────────────────────────────────────────

def draw_product_row(c, y_top, num, product, shade=False):
    x  = ML
    ws = cols()
    h  = ROW_H
    bg = colors.HexColor("#F5F5F5") if shade else colors.white

    filled_rect(c, x, y_top, CW, h, bg)

    values = [
        (str(num),                       "center"),
        (product.get("name", ""),        "left"),
        ("Товар",                        "center"),
        (f"{product.get('price','')} руб.", "center"),
        (product.get("payment", ""),     "center"),
    ]
    cx = x
    for (val, aln), w in zip(values, ws):
        rect(c, cx, y_top, w, h)
        text_in(c, cx, y_top, w, h, val, align=aln, size=FS)
        cx += w

    return y_top - h


def draw_products_header_row(c, y_top, total_products):
    """Синяя строка-заголовок блока товаров (духи и т.п.)."""
    x  = ML
    ws = cols()
    h  = ROW_H
    BG = colors.HexColor("#D6E4F0")

    filled_rect(c, x, y_top, CW, h, BG)
    cx = x
    for i, w in enumerate(ws):
        rect(c, cx, y_top, w, h)
        if i == 1:
            text_in(c, cx, y_top, w, h, f"Товары (итого {total_products} руб.)", bold=True, size=FS,
                    color=colors.HexColor("#1A5276"))
        cx += w

    return y_top - h


def draw_car_row(c, y_top, num, car, shade=False):
    x  = ML
    ws = cols()
    h  = ROW_H
    bg = colors.HexColor("#F5F5F5") if shade else colors.white

    filled_rect(c, x, y_top, CW, h, bg)

    breakdown = car.get("price_breakdown")
    if breakdown:
        from config import SERVICES
        parts = []
        for key, v in breakdown.items():
            parts.append(key if key in SERVICES else v["name"])
        service_label = "+".join(parts)
    elif car.get("service_keys"):
        from config import services_short_name
        service_label = services_short_name(car["service_keys"])
    else:
        service_label = car.get("service", "")

    payment_split = car.get("payment_split")
    if payment_split:
        payment_label = " / ".join(f"{k} {v}" for k, v in payment_split.items())
        payment_size = 7
    else:
        payment_label = car.get("payment", "")
        payment_size = FS

    values = [
        (str(num),                    "center"),
        (car.get("car", ""),          "left"),
        (service_label,               "center"),
        (f"{car.get('price','')} руб.",  "center"),
        (payment_label,               "center"),
    ]
    sizes = [FS, FS, FS, FS, payment_size]
    cx = x
    for (val, aln), w, sz in zip(values, ws, sizes):
        rect(c, cx, y_top, w, h)
        text_in(c, cx, y_top, w, h, val, align=aln, size=sz)
        cx += w

    return y_top - h


def draw_employee_row(c, y_top, name):
    """Синяя строка с именем сотрудника."""
    x  = ML
    ws = cols()
    h  = ROW_H
    BG = colors.HexColor("#D6E4F0")

    filled_rect(c, x, y_top, CW, h, BG)
    cx = x
    for i, w in enumerate(ws):
        rect(c, cx, y_top, w, h)
        if i == 1:
            text_in(c, cx, y_top, w, h, name, bold=True, size=FS,
                    color=colors.HexColor("#1A5276"))
        cx += w

    return y_top - h


def draw_subtotal_row(c, y_top, emp, washer_totals, washer_salaries):
    """Зелёная итоговая строка сотрудника."""
    x  = ML
    ws = cols()
    h  = ROW_H
    BG = colors.HexColor("#E8F8E8")

    earned = washer_totals.get(emp, 0)
    sal    = washer_salaries.get(emp, 0)
    txt    = f"Итог {emp}:   намыл {earned} руб.   →   зарплата (30%) = {sal} руб."

    filled_rect(c, x, y_top, CW, h, BG)
    cx = x
    for i, w in enumerate(ws):
        rect(c, cx, y_top, w, h)
        if i == 1:
            text_in(c, cx, y_top, CW - ws[0], h, txt, size=FS - 0.5,
                    color=colors.HexColor("#1E8449"), bold=True)
        cx += w

    return y_top - h


def draw_empty_row(c, y_top, num):
    x  = ML
    ws = cols()
    h  = ROW_H

    cx = x
    for i, w in enumerate(ws):
        rect(c, cx, y_top, w, h)
        if i == 0:
            text_in(c, cx, y_top, w, h, str(num), align="center",
                    size=FS - 1, color=colors.HexColor("#AAAAAA"))
        cx += w

    return y_top - h


# ─────────────────────────────────────────────────────────────────────────────
# ГЛАВНАЯ ФУНКЦИЯ
# ─────────────────────────────────────────────────────────────────────────────

def generate_pdf(session: dict, summary: dict, output_path: str):
    c = pdfcanvas.Canvas(output_path, pagesize=A4)
    c.setTitle(f"Касса автомойки {session['date']}")
    admin_name = get_branch_admin_name(session.get("branch", ""))

    y = PAGE_H - MT

    # ── Шапка-таблица ────────────────────────────────────────────────────────
    y = draw_header_table(c, session, summary, y)
    y -= 1 * mm  # небольшой зазор

    # ── Заголовок таблицы машин ───────────────────────────────────────────────
    y = draw_table_header(c, y)

    # ── Группировка по сотрудникам ────────────────────────────────────────────
    order  = []
    by_emp = {}
    for car in session["cars"]:
        emp = car["employee"]
        if emp not in by_emp:
            order.append(emp)
            by_emp[emp] = []
        by_emp[emp].append(car)

    row_num = 1
    shade   = False

    for idx, emp in enumerate(order):
        y = draw_employee_row(c, y, emp)

        for car in by_emp[emp]:
            y = draw_car_row(c, y, row_num, car, shade=shade)
            row_num += 1
            shade = not shade

        y = draw_subtotal_row(c, y, emp, summary["washer_totals"], summary["washer_salaries"])

        # Пустая строка-отступ между сотрудниками
        if idx < len(order) - 1:
            y = draw_empty_row(c, y, row_num)
            row_num += 1

        # Новая страница если мало места
        if y < MB + 30*mm and idx < len(order) - 1:
            c.showPage()
            y = PAGE_H - MT
            y = draw_table_header(c, y)
            shade = False

    # ── Блок товаров (духи и т.п.) — после машин, с отступом ──────────────────
    products = session.get("products", [])
    if products:
        if y < MB + 50*mm:
            c.showPage()
            y = PAGE_H - MT
            y = draw_table_header(c, y)

        # Пустая строка-отступ перед блоком товаров
        y = draw_empty_row(c, y, row_num)
        row_num += 1

        total_products = sum(p.get("price", 0) for p in products)
        y = draw_products_header_row(c, y, total_products)

        shade = False
        for product in products:
            if y < MB + 20*mm:
                c.showPage()
                y = PAGE_H - MT
                y = draw_table_header(c, y)
                shade = False
            y = draw_product_row(c, y, row_num, product, shade=shade)
            row_num += 1
            shade = not shade

    # Дополняем пустыми строками до 21
    while row_num <= 21:
        y = draw_empty_row(c, y, row_num)
        row_num += 1

    # ── Нижний колонтитул ─────────────────────────────────────────────────────
    y_foot = MB + 5 * mm
    c.setStrokeColor(BLACK)
    c.setLineWidth(0.5)
    c.line(ML, y_foot + 4*mm, PAGE_W - MR, y_foot + 4*mm)

    total_w = sum(v for v in summary["washer_totals"].values())
    admin_base = total_w + summary.get("total_products", 0)
    c.setFont(FB, FS - 0.5)
    c.setFillColor(BLACK)
    c.drawString(ML, y_foot,
        f"{admin_name} (Администратор): 10% от {admin_base} руб. (мойка {total_w} руб. + товары {summary.get('total_products', 0)} руб.) = {summary['admin_salary']} руб.")
    c.setFont(F, FS - 1.5)
    c.setFillColor(colors.grey)
    c.drawRightString(PAGE_W - MR, y_foot,
        f"Сформировано автоматически  |  {session['date']}")
    c.setFillColor(BLACK)

    c.save()
