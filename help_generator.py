"""
Генератор PDF-инструкции для бота автомойки.
Вызывается один раз (или по команде /help) и отправляется в чат.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os, sys


# ── Шрифты (те же пути что в pdf_generator.py) ──────────────────────────────

def _register_fonts():
    candidates = {"Times": [], "Times-Bold": []}
    if sys.platform == "win32":
        wf = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "Fonts")
        candidates["Times"]      = [os.path.join(wf, "times.ttf"),   os.path.join(wf, "arial.ttf")]
        candidates["Times-Bold"] = [os.path.join(wf, "timesbd.ttf"), os.path.join(wf, "arialbd.ttf")]
    elif sys.platform == "darwin":
        candidates["Times"]      = ["/Library/Fonts/Times New Roman.ttf"]
        candidates["Times-Bold"] = ["/Library/Fonts/Times New Roman Bold.ttf"]
    else:
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
    for name, paths in candidates.items():
        for path in paths:
            if os.path.exists(path):
                pdfmetrics.registerFont(TTFont(name, path))
                break

_register_fonts()

# ── Цвета ────────────────────────────────────────────────────────────────────

DARK_BLUE   = colors.HexColor("#1F3864")
MID_BLUE    = colors.HexColor("#2E5FA3")
LIGHT_BLUE  = colors.HexColor("#D6E4F0")
GREEN       = colors.HexColor("#1E8449")
LIGHT_GREEN = colors.HexColor("#E8F8E8")
GOLD        = colors.HexColor("#D4AC0D")
LIGHT_GOLD  = colors.HexColor("#FEF9E7")
GREY_BG     = colors.HexColor("#F5F5F5")
BLACK       = colors.black
WHITE       = colors.white

# ── Стили ────────────────────────────────────────────────────────────────────

F  = "Times"
FB = "Times-Bold"

def styles():
    return {
        "title": ParagraphStyle("title",
            fontName=FB, fontSize=22, leading=28,
            textColor=WHITE, alignment=TA_CENTER, spaceAfter=0),
        "subtitle": ParagraphStyle("subtitle",
            fontName=F, fontSize=11, leading=15,
            textColor=LIGHT_BLUE, alignment=TA_CENTER, spaceAfter=0),
        "section": ParagraphStyle("section",
            fontName=FB, fontSize=13, leading=17,
            textColor=DARK_BLUE, spaceBefore=6, spaceAfter=4),
        "body": ParagraphStyle("body",
            fontName=F, fontSize=10, leading=14,
            textColor=BLACK, spaceAfter=2),
        "cmd": ParagraphStyle("cmd",
            fontName=FB, fontSize=10, leading=14,
            textColor=MID_BLUE, spaceAfter=2),
        "tip": ParagraphStyle("tip",
            fontName=F, fontSize=9, leading=13,
            textColor=GREEN, spaceAfter=2),
        "note": ParagraphStyle("note",
            fontName=F, fontSize=9, leading=13,
            textColor=colors.HexColor("#7D3C98"), spaceAfter=2),
        "table_head": ParagraphStyle("table_head",
            fontName=FB, fontSize=9, leading=12,
            textColor=WHITE, alignment=TA_CENTER),
        "table_cell": ParagraphStyle("table_cell",
            fontName=F, fontSize=9, leading=12,
            textColor=BLACK),
        "table_cell_c": ParagraphStyle("table_cell_c",
            fontName=F, fontSize=9, leading=12,
            textColor=BLACK, alignment=TA_CENTER),
    }

S = styles()


# ── Вспомогательные блоки ────────────────────────────────────────────────────

def section_header(icon, title):
    """Синяя полоса-заголовок секции."""
    data = [[Paragraph(f"{icon}  {title}", S["section"])]]
    t = Table(data, colWidths=[170*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), LIGHT_BLUE),
        ("TOPPADDING",    (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("LINEBELOW", (0,0), (-1,-1), 1.5, MID_BLUE),
    ]))
    return t


def cmd_table(rows, col_widths=None):
    """Таблица: команда | описание."""
    if col_widths is None:
        col_widths = [45*mm, 125*mm]
    header = [
        Paragraph("Команда / Кнопка", S["table_head"]),
        Paragraph("Описание", S["table_head"]),
    ]
    table_data = [header]
    for cmd, desc in rows:
        table_data.append([
            Paragraph(cmd, S["cmd"]),
            Paragraph(desc, S["table_cell"]),
        ])
    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND",    (0,0), (-1,0),  DARK_BLUE),
        ("TEXTCOLOR",     (0,0), (-1,0),  WHITE),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, GREY_BG]),
        ("GRID",          (0,0), (-1,-1), 0.4, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("RIGHTPADDING",  (0,0), (-1,-1), 6),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ]
    t.setStyle(TableStyle(style))
    return t


def tip_box(text, color=LIGHT_GREEN, border=GREEN, icon="💡"):
    """Цветная подсказка."""
    data = [[Paragraph(f"{icon}  {text}", S["tip"])]]
    t = Table(data, colWidths=[170*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), color),
        ("LINEBELOW",     (0,0), (-1,-1), 0.8, border),
        ("LINEBEFORE",    (0,0), (-1,-1), 3,   border),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
    ]))
    return t


def salary_table():
    """Таблица расчёта зарплат."""
    header = [
        Paragraph("Кто", S["table_head"]),
        Paragraph("База расчёта", S["table_head"]),
        Paragraph("%", S["table_head"]),
        Paragraph("Пример (касса 10 000 ₽)", S["table_head"]),
    ]
    rows = [
        ["Мойщик", "Сумма услуг, которые он выполнил", "30%", "3 000 ₽"],
        ["Администратор", "Вся мойка + продажа товаров (духи)", "10%", "1 000 ₽"],
        ["Товары (духи)", "Идут в общую кассу", "—", "—"],
    ]
    data = [header] + [[Paragraph(c, S["table_cell_c"] if i in (1,2,3) else S["table_cell"]) for i, c in enumerate(r)] for r in rows]
    t = Table(data, colWidths=[28*mm, 60*mm, 18*mm, 64*mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  DARK_BLUE),
        ("BACKGROUND",    (0,3), (-1,3),  LIGHT_GOLD),
        ("ROWBACKGROUNDS",(0,1), (-1,2),  [WHITE, GREY_BG]),
        ("GRID",          (0,0), (-1,-1), 0.4, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("RIGHTPADDING",  (0,0), (-1,-1), 6),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    return t


def perfume_table():
    """Список духов."""
    from config import PRODUCTS
    header = [
        Paragraph("№", S["table_head"]),
        Paragraph("Название", S["table_head"]),
        Paragraph("Цена", S["table_head"]),
    ]
    rows = [[Paragraph(str(i+1), S["table_cell_c"]),
             Paragraph(p["name"], S["table_cell"]),
             Paragraph(f"{p['price']} &#8381;", S["table_cell_c"])]
            for i, p in enumerate(PRODUCTS.values())]
    t = Table([header]+rows, colWidths=[12*mm, 130*mm, 28*mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  DARK_BLUE),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, GREY_BG]),
        ("GRID",          (0,0), (-1,-1), 0.4, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("RIGHTPADDING",  (0,0), (-1,-1), 6),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    return t


# ── Главная функция ──────────────────────────────────────────────────────────

def generate_help_pdf(output_path: str):
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=20*mm, rightMargin=15*mm,
        topMargin=15*mm, bottomMargin=15*mm,
        title="Инструкция — Бот Автомойки",
    )

    story = []

    # ── ОБЛОЖКА-ШАПКА ────────────────────────────────────────────────────────
    cover_data = [[
        Paragraph("🚗  БОТ КАССЫ АВТОМОЙКИ", S["title"]),
        Paragraph("Инструкция для администраторов", S["subtitle"]),
    ]]
    cover = Table([[Paragraph("🚗  БОТ КАССЫ АВТОМОЙКИ", S["title"])],
                   [Paragraph("Инструкция для администраторов", S["subtitle"])]],
                  colWidths=[170*mm])
    cover.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), DARK_BLUE),
        ("TOPPADDING",    (0,0), (-1,0),  14),
        ("BOTTOMPADDING", (0,-1),(-1,-1), 14),
        ("LEFTPADDING",   (0,0), (-1,-1), 10),
        ("RIGHTPADDING",  (0,0), (-1,-1), 10),
        ("LINEBELOW",     (0,-1),(-1,-1), 3, GOLD),
    ]))
    story.append(cover)
    story.append(Spacer(1, 6*mm))

    # ── 1. НАЧАЛО РАБОТЫ ─────────────────────────────────────────────────────
    story.append(section_header("📋", "1. Начало работы"))
    story.append(Spacer(1, 2*mm))
    story.append(cmd_table([
        ("/start",   "Запуск бота. Показывает главное меню и текущий филиал."),
        ("/newday",  "Выбор филиала и начало новой смены. Запускать каждое утро перед началом работы."),
    ]))
    story.append(Spacer(1, 3*mm))
    story.append(tip_box("Каждое утро начинай с /newday — выбери филиал. Без этого добавить машину не получится.", icon="⚠️", color=LIGHT_GOLD, border=GOLD))
    story.append(Spacer(1, 5*mm))

    # ── 2. ДОБАВЛЕНИЕ МАШИНЫ ─────────────────────────────────────────────────
    story.append(KeepTogether([
        section_header("🚗", "2. Добавление машины"),
        Spacer(1, 2*mm),
        cmd_table([
            ("🚗 Добавить машину",
             "Кнопка меню. Открывает пошаговый мастер: Сотрудник → Тип кузова → Услуга(и) → Марка → Оплата."),
            ("Быстрый ввод текстом",
             "Написать в чат: BMW Артур комплекс нал — бот распознает автоматически. "
             "Тип кузова по умолчанию — Седан; для других добавь: кроссовер / внедорожник / микроавтобус."),
            ("Быстрый ввод пакетом",
             "Первой строкой — имя сотрудника, далее по одной машине. Пример:\n"
             "Артур\nBMW комплекс нал\nToyota ручная visa"),
        ]),
        Spacer(1, 3*mm),
    ]))

    # Таблица типов кузова
    body_data = [
        [Paragraph("Тип кузова", S["table_head"]), Paragraph("Ключевые слова", S["table_head"])],
        [Paragraph("Седан", S["table_cell"]),         Paragraph("(по умолчанию, ничего писать не нужно)", S["table_cell"])],
        [Paragraph("Кроссовер", S["table_cell"]),     Paragraph("кроссовер, кросс", S["table_cell"])],
        [Paragraph("Внедорожник", S["table_cell"]),   Paragraph("внедорожник, джип, паркетник", S["table_cell"])],
        [Paragraph("Микроавтобус", S["table_cell"]),  Paragraph("автобус, минивэн, микроавтобус", S["table_cell"])],
    ]
    bt = Table(body_data, colWidths=[50*mm, 120*mm], repeatRows=1)
    bt.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  MID_BLUE),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [WHITE, GREY_BG]),
        ("GRID",          (0,0), (-1,-1), 0.4, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("RIGHTPADDING",  (0,0), (-1,-1), 6),
    ]))
    story.append(bt)
    story.append(Spacer(1, 3*mm))
    story.append(tip_box("Комбо-услуги: в мастере можно выбрать несколько услуг сразу — цены суммируются.", icon="💡"))
    story.append(Spacer(1, 5*mm))

    # ── 3. ТОВАРЫ (ДУХИ) ─────────────────────────────────────────────────────
    story.append(section_header("🧴", "3. Продажа товаров (духи Shine Systems)"))
    story.append(Spacer(1, 2*mm))
    story.append(cmd_table([
        ("🧴 Добавить товар", "Кнопка меню. Выбери духи из списка → укажи способ оплаты."),
    ]))
    story.append(Spacer(1, 3*mm))
    story.append(perfume_table())
    story.append(Spacer(1, 3*mm))
    story.append(tip_box(
        "Товары идут в общую кассу. На зарплату мойщиков не влияют. "
        "Администратор получает 10% и с суммы продаж тоже.", icon="ℹ️",
        color=LIGHT_BLUE, border=MID_BLUE))
    story.append(Spacer(1, 5*mm))

    # ── 4. СВОДКА И СПИСОК ───────────────────────────────────────────────────
    story.append(section_header("💰", "4. Сводка, список и PDF"))
    story.append(Spacer(1, 2*mm))
    story.append(cmd_table([
        ("📋 Список",  "Показывает все машины и товары за день. Кнопки: изменить цену, удалить, лояльность."),
        ("💰 Сводка",  "Итоги дня: касса, нал/безнал/Visa, зарплаты, расходы, остаток."),
        ("📄 PDF / /pdf", "Генерирует и отправляет PDF-отчёт за текущий день. По воскресеньям — ещё и недельный отчёт."),
    ]))
    story.append(Spacer(1, 5*mm))

    # ── 5. РАСЧЁТ ЗАРПЛАТ ────────────────────────────────────────────────────
    story.append(section_header("👷", "5. Расчёт зарплат"))
    story.append(Spacer(1, 2*mm))
    story.append(salary_table())
    story.append(Spacer(1, 3*mm))
    story.append(tip_box(
        "Зарплата мойщика округляется до 50 ₽. "
        "Лояльность (скидки клиентам) учитывается при расчёте — мойщик получает % от полной цены до скидки.", icon="ℹ️",
        color=LIGHT_BLUE, border=MID_BLUE))
    story.append(Spacer(1, 5*mm))

    # ── 6. РАСХОДЫ И ЛОЯЛЬНОСТЬ ──────────────────────────────────────────────
    story.append(KeepTogether([
        section_header("💸", "6. Расходы и лояльность"),
        Spacer(1, 2*mm),
        cmd_table([
            ("💸 Расход",
             "Записать трату. Формат: химия 500. Расходы вычитаются из наличных при подсчёте остатка."),
            ("💜 Лояльность",
             "Скидка клиенту. В /list нажми «Лояльность» → выбери машину → введи сумму скидки. "
             "Лояльность справочная: в кассу не входит, но учитывается в зарплате мойщика."),
        ]),
        Spacer(1, 5*mm),
    ]))

    # ── 7. ОТЧЁТЫ ────────────────────────────────────────────────────────────
    story.append(section_header("📊", "7. Отчёты"))
    story.append(Spacer(1, 2*mm))
    story.append(cmd_table([
        ("📊 Статистика",    "Количество машин, касса, топ-3 услуги, выручка по сотрудникам за сегодня."),
        ("📊 За неделю",     "Зарплаты сотрудников по дням с понедельника по сегодня."),
        ("📅 /month июнь",   "Зарплаты по неделям за указанный месяц. Пример: /month июль 2026"),
        ("/report 28.06.2026", "PDF-отчёт за любой прошлый день из архива."),
        ("🏢 Все филиалы",   "Только для владельца. Сводка по всем филиалам за сегодня."),
    ]))
    story.append(Spacer(1, 5*mm))

    # ── 8. НАСТРОЙКИ ─────────────────────────────────────────────────────────
    story.append(KeepTogether([
        section_header("⚙️", "8. Настройки"),
        Spacer(1, 2*mm),
        cmd_table([
            ("⚙️ Настройки",         "Меню настроек текущего филиала."),
            ("➕ Сотрудник",          "Добавить нового сотрудника в список выбора."),
            ("➖ Сотрудник",          "Удалить сотрудника из филиала."),
            ("💼 % администратора",   "Изменить процент зарплаты администратора (по умолчанию 10%)."),
            ("/services",             "Показать полный прайс-лист услуг по всем типам кузова."),
        ]),
        Spacer(1, 3*mm),
        tip_box("Сотрудников добавляет только администратор филиала (или владелец).", icon="🔒",
                color=LIGHT_GOLD, border=GOLD),
        Spacer(1, 5*mm),
    ]))

    # ── 9. РЕДАКТИРОВАНИЕ И УДАЛЕНИЕ ─────────────────────────────────────────
    story.append(section_header("✏️", "9. Редактирование и удаление"))
    story.append(Spacer(1, 2*mm))
    story.append(cmd_table([
        ("✏️ Изменить цену",     "В /list → «Изменить цену» → выбери машину → введи новую цену."),
        ("/edit 3 2500",          "Быстрая смена цены машины №3 на 2500 ₽."),
        ("🗑 Удалить",            "В /list → «Удалить» → выбери машину для удаления."),
        ("/delete 3",             "Удалить машину №3."),
        ("🗑 Удалить товар",      "В /list → «Удалить товар» (появляется, если есть товары)."),
    ]))
    story.append(Spacer(1, 5*mm))

    # ── 10. СПОСОБЫ ОПЛАТЫ ───────────────────────────────────────────────────
    story.append(KeepTogether([
        section_header("💳", "10. Способы оплаты"),
        Spacer(1, 2*mm),
    ]))
    pay_data = [
        [Paragraph("Кнопка", S["table_head"]), Paragraph("Расшифровка", S["table_head"]),
         Paragraph("Идёт в остаток?", S["table_head"])],
        [Paragraph("НАЛ",    S["table_cell_c"]), Paragraph("Наличные",          S["table_cell"]), Paragraph("Да",  S["table_cell_c"])],
        [Paragraph("VISA",   S["table_cell_c"]), Paragraph("Банковская карта",   S["table_cell"]), Paragraph("Нет", S["table_cell_c"])],
        [Paragraph("БЕЗНАЛ", S["table_cell_c"]), Paragraph("Перевод/эквайринг", S["table_cell"]), Paragraph("Нет", S["table_cell_c"])],
    ]
    pt = Table(pay_data, colWidths=[30*mm, 100*mm, 40*mm], repeatRows=1)
    pt.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  DARK_BLUE),
        ("BACKGROUND",    (0,1), (-1,1),  LIGHT_GREEN),
        ("ROWBACKGROUNDS",(0,2), (-1,-1), [GREY_BG, WHITE]),
        ("GRID",          (0,0), (-1,-1), 0.4, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("RIGHTPADDING",  (0,0), (-1,-1), 6),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(pt)
    story.append(Spacer(1, 3*mm))
    story.append(tip_box(
        "Остаток = Наличные − Расходы. Безнал и Visa учитываются в кассе, но не в остатке наличных.", icon="💡"))
    story.append(Spacer(1, 5*mm))

    # ── ПОДВАЛ ───────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=1.5, color=DARK_BLUE))
    story.append(Spacer(1, 3*mm))
    footer_data = [[
        Paragraph("/help — показать эту инструкцию заново", S["tip"]),
        Paragraph("По вопросам обращайтесь к владельцу бота", S["note"]),
    ]]
    ft = Table(footer_data, colWidths=[85*mm, 85*mm])
    ft.setStyle(TableStyle([
        ("TOPPADDING",    (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 0),
        ("LEFTPADDING",   (0,0), (-1,-1), 0),
        ("RIGHTPADDING",  (0,0), (-1,-1), 0),
    ]))
    story.append(ft)

    doc.build(story)
