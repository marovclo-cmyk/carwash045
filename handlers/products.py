"""
Добавление товара (духи и т.п.) — отдельно от машин.
Не привязано к сотруднику/типу кузова, не влияет на зарплату мойщиков.
Идёт в общую кассу, и админ получает свои % с этой суммы тоже.

Шаги: Товар → Оплата → сохранение.
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from sessions import (
    get_session, save_sessions,
    try_decrement_branch_stock, increment_branch_stock, get_branch_stock, get_branch_admin,
)
from config import PRODUCTS, PAYMENT_TYPES, OWNER_ID
from handlers.admin import get_current_branch
from notify import notify_user


async def step_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    branch = get_current_branch(context)
    if not branch:
        await query.message.reply_text("⚠️ Сначала выбери филиал: /newday")
        return
    buttons = [
        [InlineKeyboardButton(f"{p['name']} — {p['price']}₽", callback_data=f"prod_{key}")]
        for key, p in PRODUCTS.items()
    ]
    await query.message.reply_text("🧴 *Выбери товар:*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons))


async def cb_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.replace("prod_", "")
    product = PRODUCTS.get(key)
    if not product:
        await query.message.reply_text("❌ Товар не найден.")
        return
    context.user_data["new_product"] = {"key": key, "name": product["name"], "price": product["price"]}
    buttons = [[InlineKeyboardButton(p.upper(), callback_data=f"prodpay_{p}") for p in PAYMENT_TYPES]]
    await query.message.reply_text(
        f"🧴 {product['name']} — {product['price']}₽\n\n💳 *Способ оплаты:*",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


def _notify_low_stock(branch: str, name: str, qty: int, min_qty: int) -> None:
    """GAP-P1: то же уведомление, что и на сайте/Mini App (webapp/server.py:
    _notify_low_stock) — админу филиала и владельцу, однократно при
    пересечении порога."""
    text = f"📦 Мало товара на складе «{branch}»: {name} — остаток {qty} (порог {min_qty})"
    admin_id = get_branch_admin(branch)
    if admin_id:
        notify_user(admin_id, text)
    if OWNER_ID and OWNER_ID != admin_id:
        notify_user(OWNER_ID, text)


async def cb_product_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    payment = query.data.replace("prodpay_", "")
    branch  = get_current_branch(context)
    if not branch:
        await query.message.reply_text("⚠️ Сначала выбери филиал: /newday")
        return
    session = get_session(branch)
    product = context.user_data.pop("new_product", None)
    if not product:
        await query.message.reply_text("❌ Что-то пошло не так, начни заново.")
        return
    # GAP-P1: если товар отслеживается на складе филиала и остатка не
    # хватает — продажа блокируется (то же правило, что на сайте/Mini App).
    ok, new_qty, crossed = try_decrement_branch_stock(branch, product["key"])
    if not ok:
        await query.message.reply_text(f"❌ Нет в наличии: {product['name']} — остаток 0")
        return
    product["payment"] = payment
    product["num"]     = len(session["products"]) + 1
    session["products"].append(product)
    save_sessions()
    stock_line = f"\n📦 Остаток: {new_qty}" if new_qty is not None else ""
    await query.message.reply_text(
        f"✅ *Товар #{product['num']} добавлен*\n"
        f"🧴 {product['name']}\n"
        f"💰 {product['price']}₽ | 💳 {payment}{stock_line}",
        parse_mode="Markdown")
    if crossed:
        stock = get_branch_stock(branch).get(product["key"], {})
        _notify_low_stock(branch, product["name"], new_qty, int(stock.get("min_qty", 0)))


async def show_products_for_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список товаров кнопками для удаления."""
    query  = update.callback_query
    branch = get_current_branch(context)
    if not branch:
        await query.message.reply_text("⚠️ Сначала выбери филиал: /newday")
        return
    session  = get_session(branch)
    products = session.get("products", [])
    if not products:
        await query.message.reply_text("📋 Товаров пока нет.")
        return
    buttons = [
        [InlineKeyboardButton(f"#{p['num']} {p['name']} — {p['price']}₽", callback_data=f"delprod_{p['num']}")]
        for p in products
    ]
    await query.message.reply_text("🗑 Выбери товар для удаления:", reply_markup=InlineKeyboardMarkup(buttons))


async def cb_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    num    = int(query.data.replace("delprod_", ""))
    branch = get_current_branch(context)
    if not branch:
        await query.message.reply_text("⚠️ Сначала выбери филиал: /newday")
        return
    session = get_session(branch)
    product = next((p for p in session["products"] if p["num"] == num), None)
    if not product:
        await query.message.reply_text(f"❌ Товар #{num} не найден.")
        return
    session["products"].remove(product)
    save_sessions()
    if product.get("key"):
        increment_branch_stock(branch, product["key"])  # GAP-P1: отмена продажи — остаток возвращается
    await query.message.reply_text(f"🗑 #{num} {product['name']} | {product['price']}₽ — удалено")
