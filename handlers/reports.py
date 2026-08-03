from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from sessions import get_session, load_archive, sessions as _sessions
from calculator import calculate_summary
from config import OWNER_ID, BRANCHES
from handlers.admin import get_current_branch

MONTHS_RU = {
    "январь":1,"февраль":2,"март":3,"апрель":4,
    "май":5,"июнь":6,"июль":7,"август":8,
    "сентябрь":9,"октябрь":10,"ноябрь":11,"декабрь":12
}


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.admin import is_allowed, get_role
    user_id = update.effective_user.id if update.message else update.callback_query.from_user.id
    msg = update.message or update.callback_query.message
    if not is_allowed(user_id):
        await msg.reply_text("⛔ Нет доступа."); return
    branch = get_current_branch(context)
    if not branch:
        await msg.reply_text("⚠️ Сначала выбери филиал: /newday"); return
    if get_role(user_id, branch) not in ("owner", "admin"):
        await msg.reply_text(
            "⛔ Общая статистика филиала доступна только администратору.\n"
            "Свою личную статистику и зарплату смотри в Mini App: /app")
        return
    from sessions import session_has_data
    session = get_session(branch)
    if not session_has_data(session):
        await msg.reply_text("📋 Нет данных за сегодня."); return
    s = calculate_summary(session)
    svc_count = {}
    for c in session["cars"]:
        svc_count[c.get("service","—")] = svc_count.get(c.get("service","—"),0) + 1
    top = sorted(svc_count.items(), key=lambda x: x[1], reverse=True)[:3]
    emp_lines = [
        f"  {e}: {sum(1 for c in session['cars'] if c['employee']==e)} маш, {v}₽"
        for e,v in s["washer_totals"].items()
    ]
    text = (
        f"📈 *Статистика за {session['date']}* | 📍 {branch}\n\n"
        f"🚗 Машин: {len(session['cars'])}\n"
        f"💰 Касса: {s['total']}₽"
        + (f" | Общая: {s['grand_total']}₽" if s['total_loyalty'] else "") +
        f"\n\n👷 *По сотрудникам:*\n" + "\n".join(emp_lines) +
        f"\n\n🔧 *Топ услуг:*\n" +
        "\n".join(f"  {i+1}. {n} — {c}р" for i,(n,c) in enumerate(top))
    )
    await msg.reply_text(text, parse_mode="Markdown")


async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.admin import is_allowed, get_role
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа."); return
    branch = get_current_branch(context)
    if not branch:
        await update.message.reply_text("⚠️ Сначала выбери филиал: /newday"); return
    if get_role(update.effective_user.id, branch) not in ("owner", "admin"):
        await update.message.reply_text("⛔ Отчёты доступны только администратору филиала."); return
    await _send_week_report(update.message, branch)


async def _send_week_report(msg, branch: str):
    from employee_stats import all_employees_period_stats, week_range
    week_start, today = week_range()
    employees = all_employees_period_stats(branch, week_start, today)

    if not employees:
        await msg.reply_text("📋 Нет данных за эту неделю."); return

    lines = [f"📊 *Зарплата {week_start.strftime('%d.%m')}–{today.strftime('%d.%m.%Y')}* | 📍 {branch}\n"]
    for emp in employees:
        lines.append(f"👷 *{emp['name']}:*")
        for d in emp["days"]:
            role_str = ", ".join(f"{r}: {a}₽" for r, a in d["roles"].items())
            lines.append(f"  {d['date']}: {role_str}")
        for role, amount in emp["by_role"].items():
            lines.append(f"  {role.capitalize()}: {amount}₽")
        lines.append(f"  *Итого: {emp['total']}₽*\n")
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


async def month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.admin import is_allowed, get_role
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа."); return
    branch = get_current_branch(context)
    if not branch:
        await update.message.reply_text("⚠️ Сначала выбери филиал: /newday"); return
    if get_role(update.effective_user.id, branch) not in ("owner", "admin"):
        await update.message.reply_text("⛔ Отчёты доступны только администратору филиала."); return
    args = context.args
    if not args:
        await update.message.reply_text("Пример: `/month июнь` или `/month июнь 2026`",
            parse_mode="Markdown"); return
    month_num = MONTHS_RU.get(args[0].lower())
    if not month_num:
        await update.message.reply_text(f"❌ Не понял месяц '{args[0]}'."); return
    year = int(args[1]) if len(args) > 1 else datetime.now().year
    from employee_stats import all_employees_period_stats, month_range, employee_month_stats_by_week
    month_start, month_end = month_range(month_num, year)
    employees = all_employees_period_stats(branch, month_start, month_end)
    if not employees:
        await update.message.reply_text(f"📋 Нет данных за {args[0]} {year}."); return
    lines = [f"📊 *Зарплата за {args[0].capitalize()} {year}* | 📍 {branch}\n"]
    for emp in employees:
        lines.append(f"👷 *{emp['name']}:*")
        by_week = employee_month_stats_by_week(branch, emp["name"], month_num, year)
        for wk, data in by_week.items():
            lines.append(f"  Неделя {wk}: {data['total']}₽")
        for role, amount in emp["by_role"].items():
            lines.append(f"  {role.capitalize()}: {amount}₽")
        lines.append(f"  Смен: {emp['shifts']} | Машин: {emp['cars']}")
        lines.append(f"  *Итого: {emp['total']}₽*\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.admin import is_allowed, get_role
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа."); return
    branch = get_current_branch(context)
    if not branch:
        await update.message.reply_text("⚠️ Сначала выбери филиал: /newday"); return
    if get_role(update.effective_user.id, branch) not in ("owner", "admin"):
        await update.message.reply_text("⛔ Отчёты доступны только администратору филиала."); return
    if not context.args:
        await update.message.reply_text("Пример: `/report 28.06.2026`", parse_mode="Markdown"); return
    date_str       = context.args[0]
    archive        = load_archive()
    day_data       = archive.get(branch, {}).get(date_str)
    if not day_data:
        await update.message.reply_text(f"❌ Нет данных за {date_str} в «{branch}»."); return
    await update.message.reply_text(f"⏳ Генерирую PDF за {date_str}...")
    summary  = calculate_summary(day_data)
    import os
    from pdf_generator import generate_pdf
    safe_branch = branch.replace(" ", "_")
    pdf_path = os.path.expanduser(f"~/report_{safe_branch}_{date_str.replace('.','')}.pdf")
    try:
        generate_pdf(day_data, summary, pdf_path)
        with open(pdf_path, "rb") as f:
            await update.message.reply_document(
                document=f, filename=f"Касса_{branch}_{date_str}.pdf",
                caption=f"📄 {date_str} | {branch} | {len(day_data['cars'])} машин | {summary['grand_total']}₽")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def allreport_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Только для owner — сводка по всем филиалам за сегодня."""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Нет доступа."); return
    msg = update.message or update.callback_query.message
    lines = ["📊 *Все филиалы — сегодня:*\n"]
    grand = 0
    for branch in BRANCHES:
        session = _sessions.get(branch)
        if not session or (not session.get("cars") and not session.get("products")):
            continue
        s = calculate_summary(session)
        grand += s["grand_total"]
        lines.append(
            f"🏢 *{branch}*\n"
            f"  Машин: {len(session['cars'])} | Касса: {s['grand_total']}₽\n"
            f"  Нал: {s['cash']}₽ | Visa: {s['visa']}₽ | Безнал: {s['beznal']}₽\n"
        )
    if len(lines) == 1:
        await msg.reply_text("📋 Нет данных по филиалам."); return
    lines.append(f"💰 *Итого по всем: {grand}₽*")
    await msg.reply_text("\n".join(lines), parse_mode="Markdown")


async def reminder_job(context):
    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text="⏰ *До закрытия 1 час!*\n\n/summary — сводка\n/pdf — PDF\n/newday — новый день",
            parse_mode="Markdown")
    except Exception as e:
        print(f"Ошибка напоминания: {e}")
