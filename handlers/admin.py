"""
Авторизация, выбор филиала и управление сотрудниками/админами филиала.

Ключевое изменение: касса общая на филиал, поэтому у каждого пользователя
в context.user_data хранится "к какому филиалу он сейчас привязан"
(current_branch) — выбирается через /newday. Список сотрудников и админ —
свойства филиала (sessions.get_branch_*), а не личной сессии.
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from sessions import (
    get_session, save_sessions, save_to_archive, reset_session,
    load_users, save_users, add_user, remove_user,
    get_branch_admin, is_branch_admin, is_branch_worker, get_role, set_branch_admin,
    get_branch_workers, add_branch_worker, remove_branch_worker,
    overwrite_archive_day, load_archive, patch_archive_fixed_rates, patch_fixed_rates,
)
from config import OWNER_ID, BRANCHES


async def fix_100726_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """РАЗОВАЯ команда: чинит запись за 10.07.2026 по филиалу «Карла Маркса»,
    в которую задним числом дописались машины из 11.07 (день случайно
    переоткрылся). Восстанавливает день по бумажному отчёту 10.07.2026.
    Можно удалить из кода после того, как один раз выполнена."""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Нет доступа.")
        return

    branch = "Карла Маркса"
    date = "10.07.2026"

    PCT_SARKIS = 5000 / 15700   # чтобы зарплата вышла ровно 5000
    PCT_ARTUR  = 3100 / 9400    # чтобы зарплата вышла ровно 3100

    day = {
        "date": date,
        "branch": branch,
        "admin_percent": 0.10,
        "admin_name": "Салим",
        "products": [],
        "incomes": [],
        "expenses": [
            {"name": "Пакеты", "amount": 250},
        ],
        "loyalty": [
            {"car_num": 3, "discount": 110},
        ],
        "cars": [
            {"num": 1,  "car": "Audi",                         "employee": "Саркис", "body_type": "sedan",
             "service_keys": ["комплекс"],                     "service": "Комплексная мойка",
             "price": 2000, "payment": "нал",
             "price_breakdown": {"комплекс": {"price": 2000, "percent": PCT_SARKIS}}},
            {"num": 2,  "car": "Captiva",                      "employee": "Саркис", "body_type": "crossover",
             "service_keys": ["комплекс"],                     "service": "Комплексная мойка",
             "price": 3200, "payment": "нал",
             "price_breakdown": {"комплекс": {"price": 3200, "percent": PCT_SARKIS}}},
            {"num": 3,  "car": "Granta",                       "employee": "Саркис", "body_type": "sedan",
             "service_keys": ["комплекс", "пластик"],          "service": "Комплексная мойка + Обработка пластика",
             "price": 2500, "payment": "visa",
             "price_breakdown": {"комплекс": {"price": 2500, "percent": PCT_SARKIS}}},
            {"num": 4,  "car": "V-Class «Премьер Групп»",      "employee": "Саркис", "body_type": "bus",
             "service_keys": ["комплекс", "твердвоск"],        "service": "Комплексная мойка + Твёрдый воск",
             "price": 4900, "payment": "безнал",
             "price_breakdown": {"комплекс": {"price": 4900, "percent": PCT_SARKIS}}},
            {"num": 5,  "car": "BMW 009",                      "employee": "Саркис", "body_type": "sedan",
             "service_keys": ["комплекс"],                     "service": "Комплексная мойка",
             "price": 2000, "payment": "нал",
             "price_breakdown": {"комплекс": {"price": 2000, "percent": PCT_SARKIS}}},
            {"num": 6,  "car": "BMW 001",                      "employee": "Саркис", "body_type": "sedan",
             "service_keys": ["ручная"],                       "service": "Ручная мойка + коврики",
             "price": 1100, "payment": "visa",
             "price_breakdown": {"ручная": {"price": 1100, "percent": PCT_SARKIS}}},

            {"num": 7,  "car": "Kia «Крым Фарминг»",           "employee": "Роман",  "body_type": "sedan",
             "service_keys": ["комплекс"],                     "service": "Комплексная мойка",
             "price": 2000, "payment": "безнал"},
            {"num": 8,  "car": "BMW",                          "employee": "Роман",  "body_type": "sedan",
             "service_keys": ["комплекс", "кожа"],             "service": "Комплексная мойка + Обработка кожи",
             "price": 2700, "payment": "visa"},
            {"num": 9,  "car": "Ford",                         "employee": "Роман",  "body_type": "sedan",
             "service_keys": ["комплекс"],                     "service": "Комплексная мойка",
             "price": 2000, "payment": "нал"},
            {"num": 10, "car": "21012 Lada",                   "employee": "Роман",  "body_type": "sedan",
             "service_keys": ["комплекс"],                     "service": "Комплексная мойка",
             "price": 2000, "payment": "безнал"},
            {"num": 11, "car": "Lexus «Элеонора»",             "employee": "Роман",  "body_type": "sedan",
             "service_keys": ["ручная"],                       "service": "Ручная мойка + коврики",
             "price": 1100, "payment": "нал"},

            {"num": 12, "car": "Omoda",                        "employee": "Артур",  "body_type": "sedan",
             "service_keys": ["комплекс"],                     "service": "Комплексная мойка",
             "price": 2500, "payment": "visa",
             "price_breakdown": {"комплекс": {"price": 2500, "percent": PCT_ARTUR}}},
            {"num": 13, "car": "V-Class «Премьер Групп»",      "employee": "Артур",  "body_type": "bus",
             "service_keys": ["комплекс", "твердвоск"],        "service": "Комплексная мойка + Твёрдый воск",
             "price": 4900, "payment": "безнал",
             "price_breakdown": {"комплекс": {"price": 4900, "percent": PCT_ARTUR}}},
            {"num": 14, "car": "Mercedes",                     "employee": "Артур",  "body_type": "sedan",
             "service_keys": ["комплекс"],                     "service": "Комплексная мойка",
             "price": 2000, "payment": "нал",
             "price_breakdown": {"комплекс": {"price": 2000, "percent": PCT_ARTUR}}},
        ],
    }

    overwrite_archive_day(branch, date, day)
    await update.message.reply_text(
        "✅ Запись за 10.07.2026 (Карла Маркса) перезаписана по бумажному отчёту.\n"
        "Зарплаты подогнаны точно под бумажку: Саркис — 5000₽, Роман — 2950₽, Артур — 3100₽.\n"
        "Проверь через /report или /allreport за 10.07."
    )


async def fix_day_rates_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/fix <дата> Имя-Сумма Имя-Сумма ... [admin-Сумма]

    Задним числом проставляет фикс-ставку ("Ставка") одному или нескольким
    сотрудникам за день. Если день уже есть в архиве (или это текущая
    открытая смена) — просто добавляет туда ставки. Если дня ВООЩЕ не было
    (за него не заводили ни одной машины) — создаёт ПУСТОЙ отчёт за этот
    день (0 машин, 0 касса) прямо в архиве и сразу проставляет ставки.

    Примеры:
      /fix 14.07.2026 Салим-1000 Саркис-1000 Роман-1000 Артур-1000
      /fix 14.07.2026 admin-1000              (ставка дежурному админу дня)
      /fix 14.07.2026 Салим-0                 (убрать ставку Салиму)

    Только владелец или админ филиала.
    """
    user_id = update.effective_user.id
    branch  = get_current_branch(context)
    if not branch:
        await update.message.reply_text("⚠️ Сначала выбери филиал: /newday")
        return
    if get_role(user_id, branch) not in ("owner", "admin"):
        await update.message.reply_text("⛔ Только администратор филиала может исправлять прошлые дни.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Формат: `/fix ДД.ММ.ГГГГ Имя-Сумма Имя-Сумма ...`\n"
            "Например:\n`/fix 14.07.2026 Салим-1000 Саркис-1000 Роман-1000 Артур-1000`\n\n"
            "Ставка администратору: `/fix 14.07.2026 admin-1000`\n"
            "Убрать ставку: `/fix 14.07.2026 Салим-0`",
            parse_mode="Markdown")
        return

    date = args[0]
    try:
        from datetime import datetime as _dt
        _dt.strptime(date, "%d.%m.%Y")
    except ValueError:
        await update.message.reply_text("⚠️ Дата должна быть в формате ДД.ММ.ГГГГ, например 14.07.2026")
        return

    rate_updates = {}
    admin_amount = None
    bad_tokens = []
    for token in args[1:]:
        if "-" not in token:
            bad_tokens.append(token)
            continue
        name, _, amount_str = token.rpartition("-")
        if not name or not amount_str.lstrip("-").isdigit():
            bad_tokens.append(token)
            continue
        amount = int(amount_str)
        if name.lower() in ("admin", "админ", "администратор"):
            admin_amount = amount
        else:
            rate_updates[name] = amount

    if bad_tokens:
        await update.message.reply_text(
            "⚠️ Не понял эти части (нужен формат Имя-Сумма): " + ", ".join(bad_tokens))
        return
    if not rate_updates and admin_amount is None:
        await update.message.reply_text("⚠️ Не нашёл ни одной пары Имя-Сумма.")
        return

    session = get_session(branch)
    if session.get("date") == date:
        # Текущий, ещё не закрытый день — правим прямо в открытой смене.
        patch_fixed_rates(session, rate_updates, admin_amount)
        save_sessions()
        applied_to = "текущую открытую смену"
    else:
        # Если такого дня в архиве ещё нет (например, за него вообще не
        # заводили ни одной машины) — /fix создаёт ПУСТОЙ отчёт за этот
        # день и сразу проставляет туда ставки, а не отказывает.
        existed_before = date in load_archive().get(branch, {})
        patch_archive_fixed_rates(branch, date, rate_updates, admin_amount, create_if_missing=True)
        applied_to = f"архивный день {date}" if existed_before else f"НОВЫЙ пустой отчёт за {date} (создан этой командой)"

    lines = [f"✅ Ставки обновлены за {applied_to} | 📍 {branch}"]
    for name, amount in rate_updates.items():
        lines.append(f"  {name}: {'убрана' if amount <= 0 else f'{amount}₽'}")
    if admin_amount is not None:
        lines.append(f"  Администратор: {'убрана' if admin_amount <= 0 else f'{admin_amount}₽'}")
    await update.message.reply_text("\n".join(lines))


PENDING: dict[int, str] = {}  # user_id -> заявленное имя (в памяти процесса)


def is_allowed(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    users = load_users()
    return str(user_id) in users


def get_current_branch(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Филиал, к которому пользователь привязан на сегодня (выбран через /newday)."""
    return context.user_data.get("current_branch")


def require_branch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Возвращает текущий филиал или None + отправляет подсказку выбрать /newday."""
    branch = get_current_branch(context)
    if not branch:
        msg = update.effective_message
        return None
    return branch


async def request_access(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str):
    """Отправляет владельцу заявку на доступ от нового пользователя."""
    user = update.effective_user
    PENDING[user.id] = name
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Разрешить", callback_data=f"approve_{user.id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"deny_{user.id}"),
    ]])
    await context.bot.send_message(
        OWNER_ID,
        f"🆕 *Заявка на доступ*\n👤 {name}\n🆔 `{user.id}`\n@{user.username or '—'}",
        parse_mode="Markdown", reply_markup=kb)
    await update.message.reply_text("✅ Заявка отправлена владельцу. Ждите подтверждения.")


async def cb_approve_deny(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("⛔ Только владелец может это решать.", show_alert=True)
        return
    await query.answer()
    action, uid_str = query.data.split("_", 1)
    approve = action == "approve"
    uid  = int(uid_str)
    name = PENDING.pop(uid, "Без имени")
    if approve:
        add_user(uid, name)
        await query.edit_message_text(f"✅ Доступ выдан: {name} (`{uid}`)", parse_mode="Markdown")
        await context.bot.send_message(uid, "✅ Доступ подтверждён! Нажми /start чтобы начать работу.")
    else:
        await query.edit_message_text(f"❌ Заявка отклонена: {name} (`{uid}`)", parse_mode="Markdown")
        await context.bot.send_message(uid, "❌ Ваша заявка на доступ отклонена.")


async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Нет доступа."); return
    if not context.args:
        await update.message.reply_text("Формат: `/adduser 123456789 Имя`", parse_mode="Markdown"); return
    try: uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом."); return
    name = " ".join(context.args[1:]) or "Без имени"
    add_user(uid, name)
    await update.message.reply_text(f"✅ Пользователь {name} ({uid}) добавлен.")


async def removeuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Нет доступа."); return
    if not context.args:
        await update.message.reply_text("Формат: `/removeuser 123456789`", parse_mode="Markdown"); return
    uid = context.args[0]
    if remove_user(int(uid)) if uid.isdigit() else False:
        await update.message.reply_text("✅ Пользователь удалён из белого списка.")
    else:
        await update.message.reply_text("❌ Пользователь не найден.")


async def listusers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Нет доступа."); return
    users = load_users()
    if not users:
        await update.message.reply_text("📋 Белый список пуст."); return
    lines = ["👥 *Белый список:*\n"]
    for uid, name in users.items():
        lines.append(f"  {name} — `{uid}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── СОТРУДНИКИ ФИЛИАЛА ───────────────────────────────────────────────────────

async def addworker_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    branch = get_current_branch(context)
    if not branch:
        await update.message.reply_text("⚠️ Сначала выбери филиал: /newday"); return
    if not is_branch_admin(update.effective_user.id, branch):
        await update.message.reply_text("⛔ Только админ филиала может добавлять сотрудников."); return
    if not context.args:
        await update.message.reply_text("Формат: `/addworker Саркис`", parse_mode="Markdown"); return
    name = " ".join(context.args)
    if add_branch_worker(branch, name):
        await update.message.reply_text(f"✅ Сотрудник *{name}* добавлен в «{branch}».", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ {name} уже есть в списке.")


async def removeworker_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    branch = get_current_branch(context)
    if not branch:
        await update.message.reply_text("⚠️ Сначала выбери филиал: /newday"); return
    if not is_branch_admin(update.effective_user.id, branch):
        await update.message.reply_text("⛔ Только админ филиала может удалять сотрудников."); return
    if not context.args:
        await update.message.reply_text("Формат: `/removeworker Саркис`", parse_mode="Markdown"); return
    name = " ".join(context.args)
    if remove_branch_worker(branch, name):
        await update.message.reply_text(f"✅ {name} удалён из «{branch}».")
    else:
        await update.message.reply_text(f"❌ {name} не найден.")


async def setadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """% администратора кассы (не путать с правами branch-admin)."""
    branch = get_current_branch(context)
    if not branch:
        await update.message.reply_text("⚠️ Сначала выбери филиал: /newday"); return
    if not is_branch_admin(update.effective_user.id, branch):
        await update.message.reply_text("⛔ Только админ филиала может менять этот параметр."); return
    if not context.args:
        await update.message.reply_text("Формат: `/setadmin 10` (процент)", parse_mode="Markdown"); return
    try: pct = float(context.args[0]) / 100
    except ValueError:
        await update.message.reply_text("❌ Укажи число."); return
    session = get_session(branch)
    session["admin_percent"] = pct
    save_sessions()
    await update.message.reply_text(f"✅ % администратора в «{branch}»: {context.args[0]}%")


async def setbranchadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner: /setbranchadmin <Филиал> <user_id> — назначить админа филиала."""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Только владелец может назначать админов филиалов."); return
    if len(context.args) < 2:
        branches_str = "\n".join(f"  • {b}" for b in BRANCHES)
        await update.message.reply_text(
            f"Формат: `/setbranchadmin Филиал 123456789`\n\nФилиалы:\n{branches_str}",
            parse_mode="Markdown")
        return
    *branch_parts, uid_str = context.args
    branch = " ".join(branch_parts)
    if branch not in BRANCHES:
        await update.message.reply_text(f"❌ Неизвестный филиал «{branch}»."); return
    try:
        uid = int(uid_str)
    except ValueError:
        await update.message.reply_text("❌ user_id должен быть числом."); return
    set_branch_admin(branch, uid)
    await update.message.reply_text(f"✅ Админ «{branch}»: `{uid}`", parse_mode="Markdown")


# ── ВЫБОР ФИЛИАЛА (/newday) ──────────────────────────────────────────────────

async def select_branch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вызывается при /newday — показывает выбор филиала."""
    buttons = [[InlineKeyboardButton(b, callback_data=f"branch_{b}")] for b in BRANCHES]
    msg = update.message or update.callback_query.message
    await msg.reply_text(
        "🏢 *Выбери филиал:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons))


async def cb_branch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    branch  = query.data.replace("branch_", "")
    user_id = query.from_user.id

    # Привязываем этого пользователя к выбранному филиалу на сегодня.
    context.user_data["current_branch"] = branch

    # Роль считаем заново для КАЖДОГО филиала — мойщик/админ одного филиала
    # не должен автоматически получать права в другом.
    role = get_role(user_id, branch)
    context.user_data["current_role"] = role
    from handlers.buttons import MAIN_MENU, WORKER_MENU
    menu = MAIN_MENU if role in ("owner", "admin") else WORKER_MENU

    from sessions import session_has_data
    session = get_session(branch)
    is_new_day = session_has_data(session) and _is_stale(session)
    if is_new_day:
        save_to_archive(branch, session)
        reset_session(branch)
        text = f"✅ *{branch}* — новый день начат!\n\nДобавляй машины через меню 🚗"
    else:
        cars_count = len(session.get("cars", []))
        text = (
            f"✅ Подключился к кассе *{branch}*\n"
            f"📅 {session.get('date','—')} | 🚗 Машин уже: {cars_count}\n\n"
            f"Если хочешь начать новый день поверх текущего — используй кнопку ниже."
        )

    await query.message.reply_text(text, parse_mode="Markdown", reply_markup=menu)

    if not is_new_day and session_has_data(session) and role in ("owner", "admin"):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Всё равно начать новый день", callback_data=f"forcenewday_{branch}")
        ]])
        await query.message.reply_text(
            "Если хочешь начать новый день поверх текущего — нажми кнопку:",
            reply_markup=kb)


async def cb_force_newday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Явное подтверждение: архивировать текущую кассу филиала и начать новую.
    Только для админа филиала/владельца — иначе мойщик мог бы случайно
    (или намеренно) обнулить общую кассу филиала."""
    query  = update.callback_query
    branch = query.data.replace("forcenewday_", "")
    if get_role(query.from_user.id, branch) not in ("owner", "admin"):
        await query.answer("⛔ Только админ филиала может начать новый день.", show_alert=True)
        return
    await query.answer()
    from sessions import session_has_data
    session = get_session(branch)
    if session_has_data(session):
        save_to_archive(branch, session)
    reset_session(branch)
    context.user_data["current_branch"] = branch
    await query.edit_message_text(
        f"✅ *{branch}* — новый день начат! Прошлый сохранён в архив.",
        parse_mode="Markdown")


def _is_stale(session: dict) -> bool:
    """Сессия считается 'вчерашней', если дата в ней не сегодняшняя —
    тогда /newday автоматически архивирует и обнуляет без лишнего вопроса.
    Если дата сегодняшняя — это просто подключение второго сотрудника
    к уже открытой кассе, ничего обнулять не нужно."""
    from datetime import datetime
    return session.get("date") != datetime.now().strftime("%d.%m.%Y")
