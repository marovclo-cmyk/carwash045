from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from handlers.cash import show_summary, show_list, loyal_start, expense_prompt
from handlers.reports import stats_command, _send_week_report, allreport_command
from handlers.admin import (
    select_branch, cb_branch, cb_force_newday, is_allowed, cb_approve_deny,
    get_current_branch, is_branch_admin, get_role,
)
from handlers.cars import (
    cb_employee, cb_body_type, cb_service, cb_payment, step_employee,
    show_cars_for_action, cb_edit_pick, cb_delete_pick,
)
from handlers.products import (
    step_product, cb_product, cb_product_payment,
    show_products_for_action, cb_delete_product,
)
from sessions import get_branch_workers
from config import PRODUCTS

MAIN_MENU = ReplyKeyboardMarkup([
    [KeyboardButton("🚗 Добавить машину"), KeyboardButton("🧴 Добавить товар")],
    [KeyboardButton("📋 Список"),          KeyboardButton("💰 Сводка")],
    [KeyboardButton("📊 Статистика"),      KeyboardButton("📄 PDF")],
    [KeyboardButton("🗂 Отчёты"),          KeyboardButton("💸 Расход")],
    [KeyboardButton("⚙️ Настройки"),       KeyboardButton("📖 Инструкция")],
], resize_keyboard=True)

# Урезанное меню для мойщика: только просмотр своего списка машин/статистики
# (зарплата видна внутри статистики) — никаких кнопок добавления/удаления/
# кассовых операций и настроек филиала. Полную личную сводку (именно "моя
# смена") мойщик смотрит в Mini App (/app), а тут — быстрый доступ в чате.
WORKER_MENU = ReplyKeyboardMarkup([
    [KeyboardButton("📋 Список")],
    [KeyboardButton("📖 Инструкция")],
], resize_keyboard=True)

# Текстовые пункты меню, доступные мойщику (не админу/владельцу филиала).
# "Список" — все машины за смену (нейтральные данные, без разбивки кассы).
# Личную статистику/зарплату мойщик смотрит только в Mini App (/api/my-stats
# уже правильно ограничен там — видно только свои машины и свою зарплату).
WORKER_ALLOWED_TEXT = {"📋 Список", "📖 Инструкция"}
# Callback-и, доступные мойщику напрямую по имени данных.
WORKER_ALLOWED_CALLBACKS = {"cmd_list"}
# Префиксы callback-данных, которые должны проходить всегда — они либо
# нужны всем (выбор филиала), либо гарантированно самопроверяются
# на права владельца/админа внутри своих обработчиков.
ALWAYS_ALLOWED_PREFIXES = ("branch_", "approve_", "deny_", "forcenewday_")


def _deny_text():
    return ("⛔ Эта функция доступна только администратору филиала.\n"
            "Для просмотра своей смены, машин и зарплаты открой Mini App: /app")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data  = query.data

    if not is_allowed(query.from_user.id):
        await query.answer("⛔ Нет доступа.", show_alert=True); return

    branch = get_current_branch(context)
    role = get_role(query.from_user.id, branch)
    if (role not in ("owner", "admin")
            and data not in WORKER_ALLOWED_CALLBACKS
            and not data.startswith(ALWAYS_ALLOWED_PREFIXES)):
        await query.answer("⛔ Доступно только администратору филиала.", show_alert=True)
        return

    await query.answer()

    if data.startswith("emp_"):        await cb_employee(update, context)
    elif data.startswith("body_"):     await cb_body_type(update, context)
    elif data.startswith("svc_"):      await cb_service(update, context)
    elif data.startswith("pay_"):      await cb_payment(update, context)
    elif data.startswith("branch_"):   await cb_branch(update, context)
    elif data.startswith("forcenewday_"): await cb_force_newday(update, context)
    elif data.startswith("approve_") or data.startswith("deny_"): await cb_approve_deny(update, context)
    elif data.startswith("edit_"):     await cb_edit_pick(update, context)
    elif data.startswith("delete_"):   await cb_delete_pick(update, context)
    elif data.startswith("loyal_"):    await _cb_loyal_pick(update, context)
    elif data.startswith("prodpay_"):  await cb_product_payment(update, context)
    elif data.startswith("prod_"):     await cb_product(update, context)
    elif data.startswith("delprod_"):  await cb_delete_product(update, context)

    elif data == "cmd_summary":  await show_summary(update, context)
    elif data == "cmd_list":     await show_list(update, context)
    elif data == "cmd_stats":    await stats_command(update, context)
    elif data == "cmd_edit":     await show_cars_for_action(update, context, "edit")
    elif data == "cmd_delete":   await show_cars_for_action(update, context, "delete")
    elif data == "cmd_deleteproduct": await show_products_for_action(update, context)
    elif data == "cmd_loyal":    await loyal_start(update, context)
    elif data == "cmd_expense":  await expense_prompt(update, context)
    elif data == "cmd_add":      await step_employee(update, context)
    elif data == "cmd_addproduct": await step_product(update, context)
    elif data == "cmd_allreport": await allreport_command(update, context)

    elif data == "cmd_pdf":
        await query.message.reply_text("Используй /pdf для генерации.")
    elif data == "cmd_week":
        branch = get_current_branch(context)
        if not branch:
            await query.message.reply_text("⚠️ Сначала выбери филиал: /newday")
        else:
            await _send_week_report(query.message, branch)

    elif data == "menu_reports":
        kb = [
            [InlineKeyboardButton("📊 За неделю", callback_data="cmd_week")],
            [InlineKeyboardButton("📅 За месяц", callback_data="help_month")],
            [InlineKeyboardButton("🗂 Архив за день", callback_data="help_report")],
            [InlineKeyboardButton("🏢 Все филиалы", callback_data="cmd_allreport")],
        ]
        await query.message.reply_text("📊 *Отчёты:*", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb))
    elif data == "help_month":
        await query.message.reply_text("Пример: `/month июнь`", parse_mode="Markdown")
    elif data == "help_report":
        await query.message.reply_text("Пример: `/report 28.06.2026`", parse_mode="Markdown")

    elif data == "menu_settings":
        await _show_settings_menu(query.message, context)
    elif data == "settings_addworker":
        await _settings_add_worker_prompt(update, context)
    elif data == "settings_removeworker":
        await _settings_remove_worker_list(update, context)
    elif data.startswith("rmworker_"):
        await _settings_remove_worker_confirm(update, context)
    elif data == "settings_setadmin":
        context.user_data["step"] = "setadmin_text"
        await query.message.reply_text("💼 Напиши новый % администратора (например: 10):")


async def _cb_loyal_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.cash import cb_loyal_pick
    await cb_loyal_pick(update, context)


# ── ⚙️ НАСТРОЙКИ ───────────────────────────────────────────────────────────

async def _show_settings_menu(msg, context: ContextTypes.DEFAULT_TYPE):
    branch = get_current_branch(context)
    if not branch:
        await msg.reply_text("⚠️ Сначала выбери филиал: /newday")
        return
    user_id = msg.chat.id  # приблизительно; точная проверка прав — ниже по data
    kb = [
        [InlineKeyboardButton("➕ Сотрудник", callback_data="settings_addworker")],
        [InlineKeyboardButton("➖ Сотрудник", callback_data="settings_removeworker")],
        [InlineKeyboardButton("💼 % администратора", callback_data="settings_setadmin")],
    ]
    workers = get_branch_workers(branch)
    workers_str = ", ".join(workers) if workers else "пока никого"
    await msg.reply_text(
        f"⚙️ *Настройки филиала «{branch}»*\n👷 Сотрудники: {workers_str}\n\n"
        f"🔧 /services — список услуг и цен",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb))


async def _settings_add_worker_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    branch = get_current_branch(context)
    if not branch:
        await query.message.reply_text("⚠️ Сначала выбери филиал: /newday"); return
    if not is_branch_admin(query.from_user.id, branch):
        await query.message.reply_text("⛔ Только админ филиала может добавлять сотрудников."); return
    context.user_data["step"] = "addworker_text"
    await query.message.reply_text("✏️ Напиши имя нового сотрудника:")


async def _settings_remove_worker_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    branch = get_current_branch(context)
    if not branch:
        await query.message.reply_text("⚠️ Сначала выбери филиал: /newday"); return
    if not is_branch_admin(query.from_user.id, branch):
        await query.message.reply_text("⛔ Только админ филиала может удалять сотрудников."); return
    workers = get_branch_workers(branch)
    if not workers:
        await query.message.reply_text("📋 Сотрудников пока нет."); return
    kb = [[InlineKeyboardButton(f"➖ {w}", callback_data=f"rmworker_{w}")] for w in workers]
    await query.message.reply_text("Кого удалить?", reply_markup=InlineKeyboardMarkup(kb))


async def _send_help_pdf(msg):
    """Генерирует и отправляет PDF-инструкцию."""
    import os
    from help_generator import generate_help_pdf
    await msg.reply_text("📖 Генерирую инструкцию...")
    path = os.path.expanduser("~/carwash_help.pdf")
    try:
        generate_help_pdf(path)
        with open(path, "rb") as f:
            await msg.reply_document(
                document=f,
                filename="Инструкция_Бот_Автомойки.pdf",
                caption="📖 Инструкция по работе с ботом")
    except Exception as e:
        await msg.reply_text(f"❌ Не удалось сгенерировать инструкцию: {e}")


async def _settings_remove_worker_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия на кнопку удаления сотрудника (rmworker_<name>)."""
    from sessions import remove_branch_worker
    query  = update.callback_query
    branch = get_current_branch(context)
    if not branch or not is_branch_admin(query.from_user.id, branch):
        await query.message.reply_text("⛔ Нет доступа."); return
    name = query.data.replace("rmworker_", "")
    if remove_branch_worker(branch, name):
        await query.message.reply_text(f"✅ {name} удалён из «{branch}».")
    else:
        await query.message.reply_text(f"❌ {name} не найден.")


async def handle_settings_text_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Обработка текстового ввода после кнопок настроек (имя сотрудника, % админа)."""
    from sessions import add_branch_worker, get_session, save_sessions
    step = context.user_data.get("step")
    branch = get_current_branch(context)

    if step == "addworker_text":
        context.user_data.pop("step", None)
        if not branch:
            await update.message.reply_text("⚠️ Сначала выбери филиал: /newday"); return True
        name = update.message.text.strip()
        if add_branch_worker(branch, name):
            await update.message.reply_text(f"✅ Сотрудник *{name}* добавлен в «{branch}».", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"⚠️ {name} уже есть в списке.")
        return True

    if step == "setadmin_text":
        context.user_data.pop("step", None)
        if not branch:
            await update.message.reply_text("⚠️ Сначала выбери филиал: /newday"); return True
        try:
            pct = float(update.message.text.strip().replace("%","")) / 100
        except ValueError:
            await update.message.reply_text("❌ Укажи число, например: 10")
            return True
        session = get_session(branch)
        session["admin_percent"] = pct
        save_sessions()
        await update.message.reply_text(f"✅ % администратора в «{branch}»: {round(pct*100)}%")
        return True

    return False


# ── ReplyKeyboard (главное меню снизу) ────────────────────────────────────

async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    text = update.message.text
    branch = get_current_branch(context)
    role = get_role(update.effective_user.id, branch)

    if role not in ("owner", "admin") and text not in WORKER_ALLOWED_TEXT:
        # Мойщик мог получить это меню, если бот перезапускался и клавиатура
        # не обновилась — на всякий случай блокируем и на уровне текста.
        if text in ("📋 Список", "📊 Статистика", "📖 Инструкция", "⚙️ Настройки",
                    "🚗 Добавить машину", "🧴 Добавить товар", "💰 Сводка", "📄 PDF",
                    "🗂 Отчёты", "💸 Расход"):
            await update.message.reply_text(_deny_text())
            return True
        return False

    if text == "🚗 Добавить машину":
        if not branch:
            await update.message.reply_text("⚠️ Сначала выбери филиал: /newday")
            return True
        workers = get_branch_workers(branch)
        if not workers:
            await update.message.reply_text("⚠️ Нет сотрудников. Админ может добавить через ⚙️ Настройки")
            return True
        buttons = [[InlineKeyboardButton(w, callback_data=f"emp_{w}")] for w in workers]
        buttons.append([InlineKeyboardButton("👤 Другой", callback_data="emp_other")])
        await update.message.reply_text("👤 *Выбери сотрудника:*", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons))
        return True

    elif text == "🧴 Добавить товар":
        if not branch:
            await update.message.reply_text("⚠️ Сначала выбери филиал: /newday")
            return True
        buttons = [
            [InlineKeyboardButton(f"{p['name']} — {p['price']}₽", callback_data=f"prod_{key}")]
            for key, p in PRODUCTS.items()
        ]
        await update.message.reply_text("🧴 *Выбери товар:*", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons))
        return True

    elif text == "📋 Список":
        await show_list(update, context); return True
    elif text == "💰 Сводка":
        await show_summary(update, context); return True
    elif text == "📊 Статистика":
        await stats_command(update, context); return True
    elif text == "📄 PDF":
        await update.message.reply_text("Используй /pdf"); return True

    elif text == "🗂 Отчёты":
        kb = [
            [InlineKeyboardButton("📊 За неделю", callback_data="cmd_week")],
            [InlineKeyboardButton("📅 За месяц", callback_data="help_month")],
            [InlineKeyboardButton("🗂 Архив за день", callback_data="help_report")],
        ]
        await update.message.reply_text("📊 *Отчёты:*", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb))
        return True

    elif text == "💸 Расход":
        context.user_data["step"] = "expense_text"
        await update.message.reply_text("💸 Напиши расход одной строкой:\nПример: `химия 500`", parse_mode="Markdown")
        return True

    elif text == "⚙️ Настройки":
        await _show_settings_menu(update.message, context)
        return True

    elif text == "📖 Инструкция":
        await _send_help_pdf(update.message)
        return True

    return False
