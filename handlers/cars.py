"""
Добавление машины — inline wizard:
  Сотрудник → Тип кузова → Услуга(и, можно комбо) → Марка → Оплата

Цена больше не вводится вручную: она автоматически берётся из прайса
(config.SERVICES) по выбранному типу кузова и суммируется при комбо услуг.
Поправить итоговую цену можно только после — кнопкой "✏️ Изменить цену"
у машины в /list (см. handlers/buttons.py), эквивалент бывшего /edit.
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from sessions import get_session, save_sessions, get_branch_workers
from config import (
    SERVICES, PAYMENT_TYPES, BODY_TYPES, BODY_TYPE_ORDER,
    services_label, services_display_name, get_service_price, get_service_percent,
)
from handlers.admin import get_current_branch


# ── ШАГ 1: СОТРУДНИК ──────────────────────────────────────────────────────

async def step_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    branch = get_current_branch(context)
    if not branch:
        await query.message.reply_text("⚠️ Сначала выбери филиал: /newday")
        return
    workers = get_branch_workers(branch)
    if not workers:
        await query.message.reply_text(
            "⚠️ В этом филиале пока нет сотрудников.\n"
            "Админ филиала может добавить: ⚙️ Настройки → ➕ Сотрудник")
        return
    buttons = [[InlineKeyboardButton(w, callback_data=f"emp_{w}")] for w in workers]
    buttons.append([InlineKeyboardButton("👤 Другой", callback_data="emp_other")])
    await query.message.reply_text("👤 *Выбери сотрудника:*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons))


async def cb_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "emp_other":
        context.user_data["step"] = "emp_other"
        await query.message.reply_text("✏️ Напиши имя сотрудника:")
        return
    employee = data.replace("emp_", "")
    context.user_data["new_car"] = {"employee": employee}
    await step_body_type(query.message, context)


async def step_body_type(msg, context: ContextTypes.DEFAULT_TYPE):
    buttons = [[InlineKeyboardButton(BODY_TYPES[bt], callback_data=f"body_{bt}")] for bt in BODY_TYPE_ORDER]
    await msg.reply_text("🚙 *Тип кузова:*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons))


async def cb_body_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    body_type = query.data.replace("body_", "")
    car = context.user_data.setdefault("new_car", {})
    car["body_type"] = body_type
    car["service_keys"] = []
    await query.message.reply_text(
        f"🚙 {BODY_TYPES[body_type]}\n\n🔧 *Выбери услугу (можно несколько — комбо):*",
        parse_mode="Markdown",
        reply_markup=build_service_keyboard([], body_type))


# ── ШАГ 2: УСЛУГА(И) ──────────────────────────────────────────────────────

def build_service_keyboard(selected: list[str], body_type: str, custom: list[dict] | None = None) -> InlineKeyboardMarkup:
    custom = custom or []
    buttons, row = [], []
    for key, svc in SERVICES.items():
        price = get_service_price(key, body_type)
        mark  = "✅ " if key in selected else ""
        row.append(InlineKeyboardButton(f"{mark}{svc['name']} ({price}₽)", callback_data=f"svc_{key}"))
        if len(row) == 1:
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("➕ Другое (своя услуга)", callback_data="svc_custom")])
    for i, c in enumerate(custom):
        buttons.append([InlineKeyboardButton(
            f"✅ {c['name']} ({c['price']}₽, {int(c['percent']*100)}%) ✖",
            callback_data=f"svc_customdel_{i}")])
    total = sum(get_service_price(k, body_type) for k in selected) + sum(c["price"] for c in custom)
    done_label = "▶️ Готово" + (f" — {total}₽" if (selected or custom) else "")
    buttons.append([InlineKeyboardButton(done_label, callback_data="svc_done")])
    return InlineKeyboardMarkup(buttons)


async def cb_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    car = context.user_data.setdefault("new_car", {})
    body_type = car.get("body_type")
    selected  = car.setdefault("service_keys", [])
    custom    = car.setdefault("custom_services", [])

    if query.data == "svc_custom":
        context.user_data["step"] = "custom_svc_name"
        await query.message.reply_text("✏️ Напиши название услуги:")
        return

    if query.data.startswith("svc_customdel_"):
        idx = int(query.data.replace("svc_customdel_", ""))
        if 0 <= idx < len(custom):
            custom.pop(idx)
        await query.edit_message_reply_markup(reply_markup=build_service_keyboard(selected, body_type, custom))
        return

    if query.data == "svc_done":
        if not selected and not custom:
            await query.answer("Выбери хотя бы одну услугу", show_alert=True)
            return
        breakdown = {}
        for k in selected:
            breakdown[k] = {"name": SERVICES[k]["name"], "price": get_service_price(k, body_type),
                             "percent": get_service_percent(k)}
        for i, c in enumerate(custom):
            breakdown[f"custom_{i}"] = {"name": c["name"], "price": c["price"], "percent": c["percent"]}
        car["price_breakdown"] = breakdown
        car["service"] = " + ".join(v["name"] for v in breakdown.values())
        car["price"]   = sum(v["price"] for v in breakdown.values())
        context.user_data["step"] = "car_model"
        price_str = " + ".join(f"{v['name']}: {v['price']}₽" for v in breakdown.values())
        await query.message.reply_text(
            f"💰 {price_str}\n*Итого: {car['price']}₽*\n\n🚗 Напиши марку машины:",
            parse_mode="Markdown")
        return

    svc_key = query.data.replace("svc_", "")
    if svc_key in selected:
        selected.remove(svc_key)
    else:
        selected.append(svc_key)
    await query.edit_message_reply_markup(reply_markup=build_service_keyboard(selected, body_type, custom))


# ── ШАГ 3: МАРКА (текст) ──────────────────────────────────────────────────

async def handle_text_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    step = context.user_data.get("step")

    if step == "emp_other":
        employee = update.message.text.strip()
        context.user_data.pop("step")
        context.user_data["new_car"] = {"employee": employee}
        await step_body_type(update.message, context)
        return True

    if step == "custom_svc_name":
        name = update.message.text.strip()
        if not name:
            await update.message.reply_text("❌ Название не может быть пустым. Напиши ещё раз:")
            return True
        context.user_data["custom_svc_name"] = name
        context.user_data["step"] = "custom_svc_price"
        await update.message.reply_text(f"💰 Напиши цену услуги «{name}» (в рублях):")
        return True

    if step == "custom_svc_price":
        try:
            price = int(update.message.text.strip().replace("₽", "").replace("р", ""))
        except ValueError:
            await update.message.reply_text("❌ Введи число. Пример: 1500")
            return True
        context.user_data["custom_svc_price"] = price
        context.user_data["step"] = "custom_svc_percent"
        await update.message.reply_text("📊 Напиши процент зарплаты мойщику за эту услугу (например: 30):")
        return True

    if step == "custom_svc_percent":
        txt = update.message.text.strip().replace("%", "").replace(",", ".")
        try:
            pct = float(txt)
        except ValueError:
            await update.message.reply_text("❌ Введи число. Пример: 30")
            return True
        if not (0 <= pct <= 100):
            await update.message.reply_text("❌ Процент должен быть от 0 до 100.")
            return True
        car = context.user_data.setdefault("new_car", {})
        body_type = car.get("body_type")
        name  = context.user_data.pop("custom_svc_name", "Другое")
        price = context.user_data.pop("custom_svc_price", 0)
        car.setdefault("custom_services", []).append({"name": name, "price": price, "percent": pct / 100})
        context.user_data["step"] = None
        selected = car.get("service_keys", [])
        custom   = car.get("custom_services", [])
        await update.message.reply_text(
            f"✅ Добавлена услуга «{name}» — {price}₽ ({int(pct)}%)\n\n"
            f"🔧 *Выбери ещё услугу или жми Готово:*",
            parse_mode="Markdown",
            reply_markup=build_service_keyboard(selected, body_type, custom))
        return True

    if step == "car_model":
        car = context.user_data["new_car"]
        car["car"] = update.message.text.strip()
        context.user_data["step"] = "payment"
        buttons = [[InlineKeyboardButton(p.upper(), callback_data=f"pay_{p}") for p in PAYMENT_TYPES]]
        await update.message.reply_text("💳 *Способ оплаты:*", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons))
        return True

    if step == "edit_price":
        return await _handle_edit_price_text(update, context)

    return False


# ── ШАГ 4: ОПЛАТА → СОХРАНЕНИЕ ────────────────────────────────────────────

async def cb_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    payment = query.data.replace("pay_", "")
    branch  = get_current_branch(context)
    if not branch:
        await query.message.reply_text("⚠️ Сначала выбери филиал: /newday")
        return
    session = get_session(branch)
    car = context.user_data.pop("new_car", {})
    car["payment"] = payment
    car["num"]     = len(session["cars"]) + 1
    context.user_data.pop("step", None)
    session["cars"].append(car)
    save_sessions()
    breakdown = car.get("price_breakdown")
    if breakdown:
        percents = sorted({v["percent"] for v in breakdown.values()})
        pct = "+".join(f"{int(p*100)}%" for p in percents)
    else:
        pct = services_label(car.get("service_keys") or [])
    body_label = BODY_TYPES.get(car.get("body_type"), "")
    await query.message.reply_text(
        f"✅ *#{car['num']} добавлена*\n"
        f"🚗 {car['car']} ({body_label}) | 👷 {car['employee']}\n"
        f"🔧 {car['service']} ({pct})\n"
        f"💰 {car['price']}₽ | 💳 {payment}\n"
        f"Всего: {car['num']}",
        parse_mode="Markdown")


# ── БЫСТРЫЙ ТЕКСТОВЫЙ ВВОД ("BMW Артур комплекс ..." без указания кузова) ──
# Без кузова цену взять неоткуда — для быстрого ввода используем цену
# седана как базовую (самый частый случай), остальное правится через
# wizard или ✏️ Изменить цену.

def parse_car_from_text(text: str, session: dict, branch: str) -> dict | None:
    parts    = text.split()
    workers  = get_branch_workers(branch)
    employee = next((w for w in workers if w.lower() in text.lower()), None)
    if not employee:
        return None

    service_keys, svc_tokens = [], []
    for part in parts:
        sub_low = part.lower().strip("«»\"'.,")
        sub_parts = sub_low.split("+")
        if all(sp in SERVICES for sp in sub_parts) and sub_parts:
            for sp in sub_parts:
                if sp not in service_keys:
                    service_keys.append(sp)
            svc_tokens.append(part)

    if not service_keys:
        return None

    tokens_low = [p.lower().strip("«»\"'.,") for p in parts]
    payment = next((p for p in PAYMENT_TYPES if p.lower() in tokens_low), "нал")

    # Тип кузова по умолчанию для быстрого ввода — седан (самый частый).
    body_type = "sedan"
    for part in parts:
        low = part.lower()
        if low in ("кроссовер", "кросс"):
            body_type = "crossover"
        elif low in ("внедорожник", "джип", "паркетник"):
            body_type = "suv"
        elif low in ("автобус", "минивэн", "микроавтобус"):
            body_type = "bus"

    price = sum(get_service_price(k, body_type) for k in service_keys)

    skip = {employee.lower(), payment.lower()} | {t.lower() for t in svc_tokens}
    skip |= {"кроссовер","кросс","внедорожник","джип","паркетник","автобус","минивэн","микроавтобус"}
    car_model = " ".join(p for p in parts if p.lower() not in skip and not p.isdigit()) or "Авто"

    return {
        "num": len(session["cars"]) + 1,
        "car": car_model, "employee": employee,
        "body_type": body_type,
        "service_keys": service_keys,
        "service": services_display_name(service_keys),
        "price": price, "payment": payment,
    }


# ── РЕДАКТИРОВАНИЕ / УДАЛЕНИЕ (кнопками) ──────────────────────────────────

async def show_cars_for_action(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    """Показывает список машин кнопками для выбора (action: 'edit'|'delete')."""
    query  = update.callback_query
    branch = get_current_branch(context)
    if not branch:
        await query.message.reply_text("⚠️ Сначала выбери филиал: /newday")
        return
    session = get_session(branch)
    cars = session.get("cars", [])
    if not cars:
        await query.message.reply_text("📋 Список пуст.")
        return
    buttons = [
        [InlineKeyboardButton(f"#{c['num']} {c['car']} — {c['price']}₽", callback_data=f"{action}_{c['num']}")]
        for c in cars
    ]
    title = "✏️ Выбери машину для изменения цены:" if action == "edit" else "🗑 Выбери машину для удаления:"
    await query.message.reply_text(title, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_edit_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    num = int(query.data.replace("edit_", ""))
    context.user_data["step"] = "edit_price"
    context.user_data["edit_car_num"] = num
    await query.message.reply_text(f"✏️ Напиши новую цену для #{num}:")


async def _handle_edit_price_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    branch = get_current_branch(context)
    if not branch:
        await update.message.reply_text("⚠️ Сначала выбери филиал: /newday")
        context.user_data.pop("step", None)
        return True
    try:
        price = int(update.message.text.strip().replace("₽", "").replace("р", ""))
    except ValueError:
        await update.message.reply_text("❌ Введи число. Пример: 2500")
        return True
    num = context.user_data.pop("edit_car_num", None)
    context.user_data.pop("step", None)
    session = get_session(branch)
    car = next((c for c in session["cars"] if c["num"] == num), None)
    if not car:
        await update.message.reply_text(f"❌ Машина #{num} не найдена.")
        return True
    old = car["price"]
    car["price"] = price
    car.pop("price_breakdown", None)
    save_sessions()
    await update.message.reply_text(f"✅ #{num} {car['car']}: {old}₽ → {price}₽")
    return True


async def cb_delete_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    num    = int(query.data.replace("delete_", ""))
    branch = get_current_branch(context)
    if not branch:
        await query.message.reply_text("⚠️ Сначала выбери филиал: /newday")
        return
    session = get_session(branch)
    car = next((c for c in session["cars"] if c["num"] == num), None)
    if not car:
        await query.message.reply_text(f"❌ Машина #{num} не найдена.")
        return
    session["cars"].remove(car)
    session["loyalty"] = [l for l in session.get("loyalty", []) if l["car_num"] != num]
    save_sessions()
    await query.message.reply_text(f"🗑 #{num} {car['car']} | {car['price']}₽ — удалено")


# ── /edit и /delete как текстовые команды (для тех, кто привык) ──────────

async def edit_car_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.admin import is_allowed, get_role
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа."); return
    branch = get_current_branch(context)
    if not branch:
        await update.message.reply_text("⚠️ Сначала выбери филиал: /newday"); return
    if get_role(update.effective_user.id, branch) not in ("owner", "admin"):
        await update.message.reply_text("⛔ Только админ филиала может редактировать машины."); return
    session = get_session(branch)
    args    = context.args
    if len(args) < 2:
        await update.message.reply_text("Формат: `/edit 3 2500`", parse_mode="Markdown"); return
    try: num, price = int(args[0]), int(args[1])
    except ValueError:
        await update.message.reply_text("❌ Неверный формат."); return
    car = next((c for c in session["cars"] if c["num"] == num), None)
    if not car:
        await update.message.reply_text(f"❌ Машина #{num} не найдена."); return
    old = car["price"]; car["price"] = price; car.pop("price_breakdown", None); save_sessions()
    await update.message.reply_text(f"✅ #{num} {car['car']}: {old}₽ → {price}₽")


async def delete_car_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.admin import is_allowed, get_role
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа."); return
    branch = get_current_branch(context)
    if not branch:
        await update.message.reply_text("⚠️ Сначала выбери филиал: /newday"); return
    if get_role(update.effective_user.id, branch) not in ("owner", "admin"):
        await update.message.reply_text("⛔ Только админ филиала может удалять машины."); return
    session = get_session(branch)
    if not context.args:
        await update.message.reply_text("Формат: `/delete 3`", parse_mode="Markdown"); return
    try: num = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Укажи номер."); return
    car = next((c for c in session["cars"] if c["num"] == num), None)
    if not car:
        await update.message.reply_text(f"❌ Машина #{num} не найдена."); return
    session["cars"].remove(car)
    session["loyalty"] = [l for l in session.get("loyalty",[]) if l["car_num"] != num]
    save_sessions()
    await update.message.reply_text(f"🗑 #{num} {car['car']} | {car['price']}₽ — удалено")
