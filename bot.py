from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
import os
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from datetime import time as dtime
import logging

from config import TOKEN, OWNER_ID
from sessions import load_sessions, get_session, save_sessions, get_branch_workers
from calculator import calculate_summary
from handlers.admin import (
    is_allowed, adduser_command, removeuser_command, listusers_command,
    addworker_command, removeworker_command, setadmin_command, setbranchadmin_command,
    select_branch, get_current_branch, cb_approve_deny, cb_branch, cb_force_newday,
    fix_100726_command,
    fix_day_rates_command,
)
from handlers.cars import (
    edit_car_command, delete_car_command, handle_text_step, parse_car_from_text,
)
from handlers.cash import (
    loyal_command, expense_command, income_command, show_summary, show_list, handle_loyal_text,
    handle_expense_step_text, handle_income_step_text,
)
from handlers.reports import (
    stats_command, week_command, month_command, report_command,
    allreport_command, reminder_job,
)
from handlers.buttons import button_callback, handle_menu_text, handle_settings_text_step, MAIN_MENU

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        context.user_data["step"] = "awaiting_name"
        await update.message.reply_text(
            "👋 Привет! У тебя пока нет доступа.\n✏️ Напиши своё имя — отправлю заявку владельцу:")
        return
    branch = get_current_branch(context)
    from handlers.admin import get_role
    from handlers.buttons import MAIN_MENU as _MAIN_MENU, WORKER_MENU as _WORKER_MENU
    role = get_role(update.effective_user.id, branch)
    menu = _MAIN_MENU if role in ("owner", "admin") else _WORKER_MENU
    branch_line = f"\n📍 Текущий филиал: *{branch}*" if branch else "\nНачни с /newday чтобы выбрать филиал."
    await update.message.reply_text(
        f"👋 *Касса автомойки запущена!*\n━━━━━━━━━━━━━━━━{branch_line}",
        parse_mode="Markdown", reply_markup=menu)


async def newday_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа."); return
    await select_branch(update, context)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа."); return
    from handlers.buttons import _send_help_pdf
    await _send_help_pdf(update.message)


async def pdf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    branch = get_current_branch(context)
    if not branch:
        await update.message.reply_text("⚠️ Сначала выбери филиал: /newday"); return
    from handlers.admin import get_role
    if get_role(update.effective_user.id, branch) not in ("owner", "admin"):
        await update.message.reply_text("⛔ PDF кассы доступен только администратору филиала."); return
    session = get_session(branch)
    if not session["cars"] and not session.get("products"):
        await update.message.reply_text("📋 Нет данных."); return
    await update.message.reply_text("⏳ Генерирую PDF...")
    import os
    from pdf_generator import generate_pdf
    from datetime import datetime
    summary = calculate_summary(session)
    safe_branch = branch.replace(" ", "_")
    pdf_path = os.path.expanduser(f"~/kassa_{safe_branch}_{datetime.now().strftime('%d%m%Y_%H%M')}.pdf")
    try:
        generate_pdf(session, summary, pdf_path)
        with open(pdf_path, "rb") as f:
            await update.message.reply_document(
                document=f, filename=f"Касса_{branch}_{session['date']}.pdf",
                caption=f"✅ {session['date']} | 📍 {branch} | {len(session['cars'])} машин | {summary['grand_total']}₽")
        if datetime.now().weekday() == 6:
            await update.message.reply_text("📊 Воскресенье — отправляю недельный отчёт...")
            from handlers.reports import _send_week_report
            await _send_week_report(update.message, branch)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def services_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа."); return
    from config import SERVICES, BODY_TYPE_ORDER, BODY_TYPES, get_service_price
    lines = ["🔧 *Прайс-лист:*\n"]
    header = "Услуга".ljust(28) + " | " + " / ".join(BODY_TYPES[b][:4] for b in BODY_TYPE_ORDER)
    lines.append(f"`{header}`")
    for key, svc in SERVICES.items():
        prices = svc["prices"]
        if isinstance(prices, dict):
            price_str = " / ".join(str(get_service_price(key, b)) for b in BODY_TYPE_ORDER)
        else:
            price_str = f"{prices} (для всех)"
        lines.append(f"  `{key}` — {svc['name']}: {price_str}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("step") == "awaiting_name":
        context.user_data.pop("step", None)
        name = update.message.text.strip()
        from handlers.admin import request_access
        await request_access(update, context, name)
        return

    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа."); return

    if await handle_menu_text(update, context): return
    if await handle_text_step(update, context): return
    if await handle_loyal_text(update, context): return
    if await handle_expense_step_text(update, context): return
    if await handle_income_step_text(update, context): return
    if await handle_settings_text_step(update, context): return

    branch = get_current_branch(context)

    from handlers.admin import get_role
    if get_role(update.effective_user.id, branch) not in ("owner", "admin"):
        await update.message.reply_text(
            "⛔ Добавлять машины/расходы можно только через админа филиала.\n"
            "Свою смену, машины и зарплату смотри в Mini App: /app")
        return

    if not branch:
        await update.message.reply_text(
            "⚠️ Сначала выбери филиал: /newday\n\n"
            "После этого можно писать машины текстом, например:\n"
            "`BMW Артур комплекс нал`", parse_mode="Markdown")
        return

    text    = update.message.text.strip()
    session = get_session(branch)

    if text.lower().startswith("расход"):
        await expense_command(update, context); return

    if text.lower().startswith("доход"):
        await income_command(update, context); return

    lines   = [l.strip() for l in text.splitlines() if l.strip()]
    workers = get_branch_workers(branch)

    if len(lines) > 1 and any(lines[0].lower() == w.lower() for w in workers):
        employee = next(w for w in workers if w.lower() == lines[0].lower())
        results, errors = [], []
        for line in lines[1:]:
            if any(line.lower() == w.lower() for w in workers):
                employee = next(w for w in workers if w.lower() == line.lower())
                continue
            line_full = f"{line} {employee}" if not any(w.lower() in line.lower() for w in workers) else line
            car = parse_car_from_text(line_full, session, branch)
            if car:
                session["cars"].append(car); results.append(car)
            else:
                errors.append(line)
        save_sessions()
        if results:
            out = [f"✅ *Добавлено {len(results)} машин:*\n"]
            for r in results:
                out.append(f"#{r['num']} {r['car']} | {r['employee']} | {r['price']}₽")
            if errors: out.append(f"\n⚠️ Не распознаны: {', '.join(errors)}")
            await update.message.reply_text("\n".join(out), parse_mode="Markdown")
        return

    car = parse_car_from_text(text, session, branch)
    if car:
        session["cars"].append(car); save_sessions()
        from config import services_label
        pct = services_label(car.get("service_keys") or [])
        await update.message.reply_text(
            f"✅ *#{car['num']} добавлена*\n"
            f"🚗 {car['car']} | 👷 {car['employee']}\n"
            f"🔧 {car['service']} ({pct})\n"
            f"💰 {car['price']}₽ | 💳 {car['payment']}",
            parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "❓ Не понял. Напиши: `BMW Артур комплекс нал`\n"
            "Или используй кнопку 🚗 Добавить машину", parse_mode="Markdown")


def main():
    from config import PROXY_URL
    from telegram.request import HTTPXRequest

    WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()

    async def app_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_allowed(update.effective_user.id):
            await update.message.reply_text("⛔ Нет доступа."); return
        if not WEBAPP_URL:
            await update.message.reply_text("⚠️ WEBAPP_URL не задан в .env — сначала задеплой webapp/ и укажи адрес.")
            return
        import time
        branch = get_current_branch(context) or ""
        cache_bust = int(time.time())
        url = f"{WEBAPP_URL}?v={cache_bust}" + (f"&startapp={branch}" if branch else "")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📱 Открыть приложение", web_app=WebAppInfo(url=url))]])
        await update.message.reply_text("Жми, чтобы открыть панель:", reply_markup=kb)

    request = HTTPXRequest(
        connection_pool_size=20,
        connect_timeout=30,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=30,
        proxy=PROXY_URL or None,
    )
    builder = Application.builder().token(TOKEN).request(request).concurrent_updates(True)
    app = builder.build()
    if PROXY_URL:
        print(f"🌐 Прокси включён: {PROXY_URL}")
    else:
        print("🌐 Прокси не задан (PROXY_URL пуст в .env) — прямое подключение")

    for cmd, fn in [
        ("start",          start),
        ("help",           help_command),
        ("newday",         newday_command),
        ("pdf",            pdf_command),
        ("edit",           edit_car_command),
        ("delete",         delete_car_command),
        ("loyal",          loyal_command),
        ("summary",        show_summary),
        ("list",           show_list),
        ("stats",          stats_command),
        ("week",           week_command),
        ("month",          month_command),
        ("report",         report_command),
        ("allreport",      allreport_command),
        ("services",       services_command),
        ("setadmin",       setadmin_command),
        ("setbranchadmin", setbranchadmin_command),
        ("addworker",      addworker_command),
        ("removeworker",   removeworker_command),
        ("adduser",        adduser_command),
        ("removeuser",     removeuser_command),
        ("listusers",      listusers_command),
        ("app",            app_command),
        ("fix100726",      fix_100726_command),
        ("fix",            fix_day_rates_command),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    try:
        import pytz
        moscow = pytz.timezone("Europe/Moscow")
        app.job_queue.run_daily(reminder_job, time=dtime(19, 0, 0, tzinfo=moscow))
        print("⏰ Напоминание: 19:00 МСК")
    except Exception as e:
        print(f"⚠️ JobQueue недоступен: {e}")

    load_sessions()
    print("🚗 Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
