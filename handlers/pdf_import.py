"""
Импорт PDF-отчёта «Касса» в архив — сценарий в боте.

Пользователь (владелец/админ филиала) присылает боту PDF (свой же отчёт,
сгенерированный ранее через /pdf). Бот:
  1. скачивает и парсит его (pdf_importer.parse_kassa_pdf),
  2. пытается определить филиал по имени администратора из PDF
     (сверяя с sessions.get_branch_admin_names по всем филиалам) —
     если однозначно не получилось, просит выбрать филиал кнопками,
  3. показывает сводку и просит подтверждения (предупреждает, если день
     с такой датой уже есть в архиве — импорт его перезапишет),
  4. по подтверждению — sessions.overwrite_archive_day(...).

Ничего не пишет в архив без явного подтверждения пользователя.
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import BRANCHES
from sessions import get_branch_admin_names, load_archive, overwrite_archive_day
from handlers.admin import is_allowed, get_role, get_current_branch
from pdf_importer import parse_kassa_pdf, PdfImportError

_PENDING_KEY = "pdf_import_pending"


def _guess_branches(admin_name: str) -> list[str]:
    if not admin_name:
        return []
    return [b for b in BRANCHES if admin_name in get_branch_admin_names(b)]


def _summary_text(day: dict, branch: str | None) -> str:
    cars = day["cars"]
    total = sum(c["price"] for c in cars) + sum(p["price"] for p in day["products"])
    lines = [
        "📄 *Найден отчёт «Касса»*",
        f"📅 Дата: *{day['date']}*",
        f"🏢 Филиал: *{branch or '— не определён, выбери ниже —'}*",
        f"🚗 Машин: {len(cars)}" + (f" | 🧴 Товаров: {len(day['products'])}" if day["products"] else ""),
        f"💰 Выручка (без вычета скидок): {total}₽",
    ]
    if day["fixed_rates"]:
        sal = ";  ".join(f"{e} — {s}₽" for e, s in day["fixed_rates"].items())
        lines.append(f"👷 Зарплаты мойщиков: {sal}")
    if day["admin_name"]:
        lines.append(f"🧑‍💼 Администратор: {day['admin_name']} — {day['admin_fixed_rate']}₽")
    if day["expenses"]:
        lines.append(f"💸 Расходы: {sum(e['amount'] for e in day['expenses'])}₽")
    if day["incomes"]:
        lines.append(f"➕ Доходы: {sum(i['amount'] for i in day['incomes'])}₽")
    for w in day.get("_warnings", []):
        lines.append(w)
    return "\n".join(lines)


async def handle_pdf_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_allowed(user_id):
        return  # тихо игнорируем — как и остальной бот для неавторизованных

    branch_ctx = get_current_branch(context)
    role = get_role(user_id, branch_ctx)
    if role not in ("owner", "admin"):
        await update.message.reply_text(
            "⛔ Импорт PDF в архив доступен только владельцу или администратору филиала.")
        return

    doc = update.message.document
    if not doc or not (doc.file_name or "").lower().endswith(".pdf"):
        return

    status = await update.message.reply_text("⏳ Читаю PDF…")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        file_bytes = bytes(await tg_file.download_as_bytearray())
    except Exception as e:
        await status.edit_text(f"❌ Не удалось скачать файл: {e}")
        return

    try:
        day = parse_kassa_pdf(file_bytes)
    except PdfImportError as e:
        await status.edit_text(f"❌ {e}")
        return
    except Exception as e:
        await status.edit_text(f"❌ Не получилось разобрать PDF: {e}")
        return

    candidates = _guess_branches(day.get("_admin_name_hint") or "")

    context.user_data[_PENDING_KEY] = day

    if len(candidates) == 1:
        day["branch"] = candidates[0]
        await status.delete()
        await _ask_confirm(update.message, context, day)
    else:
        await status.delete()
        note = ("не нашёл филиал, где " + (day.get("_admin_name_hint") or "этот админ") + " числится администратором"
                if not candidates else "нашлось несколько филиалов с таким админом")
        buttons = [[InlineKeyboardButton(b, callback_data=f"impbr_{b}")] for b in BRANCHES]
        buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="impcancel")])
        await update.message.reply_text(
            _summary_text(day, None) + f"\n\n🏢 Не смог определить филиал автоматически ({note}) — выбери вручную:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons))


async def _ask_confirm(message, context: ContextTypes.DEFAULT_TYPE, day: dict):
    branch = day["branch"]
    archive = load_archive()
    exists = branch in archive and day["date"] in archive[branch]
    text = _summary_text(day, branch)
    if exists:
        text += f"\n\n⚠️ *День {day['date']} уже есть в архиве филиала «{branch}» — импорт полностью его перезапишет.*"
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Сохранить в архив", callback_data="impconfirm"),
        InlineKeyboardButton("❌ Отмена", callback_data="impcancel"),
    ]])
    await message.reply_text(text, parse_mode="Markdown", reply_markup=buttons)


async def cb_import_branch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day = context.user_data.get(_PENDING_KEY)
    if not day:
        await query.edit_message_text("⚠️ Данные импорта устарели, пришли PDF ещё раз.")
        return
    branch = query.data.replace("impbr_", "")
    day["branch"] = branch
    await query.message.delete()
    await _ask_confirm(query.message, context, day)


async def cb_import_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day = context.user_data.pop(_PENDING_KEY, None)
    if not day:
        await query.edit_message_text("⚠️ Данные импорта устарели, пришли PDF ещё раз.")
        return

    branch = day["branch"]
    day.pop("_warnings", None)
    day.pop("_admin_name_hint", None)

    overwrite_archive_day(branch, day["date"], day)

    try:
        from history_log import log_action
        log_action(branch, "pdf_import", update.effective_user.id,
                    update.effective_user.full_name, details=f"дата {day['date']}")
    except Exception:
        pass

    await query.edit_message_text(
        f"✅ День *{day['date']}* филиала *{branch}* сохранён в архив из PDF.",
        parse_mode="Markdown")


async def cb_import_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop(_PENDING_KEY, None)
    await query.edit_message_text("🚫 Импорт отменён.")
