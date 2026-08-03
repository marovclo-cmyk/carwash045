"""
Отправка уведомлений пользователю от имени бота (например, когда его
назначили админом филиала или добавили как сотрудника с доступом).
Работает "по возможности": если отправка не удалась (бот не может писать
пользователю, пока тот не запустил бота — ограничение Telegram) — тихо
логируем и не роняем запрос.
"""
import asyncio
import logging

from telegram import Bot
from telegram.error import TelegramError

from config import TOKEN

log = logging.getLogger("notify")
_bot = Bot(token=TOKEN)


async def _send(user_id: int, text: str) -> bool:
    try:
        await _bot.send_message(chat_id=user_id, text=text)
        return True
    except TelegramError as e:
        log.warning("Не удалось отправить уведомление %s: %s", user_id, e)
        return False


def notify_user(user_id: int, text: str) -> None:
    """Fire-and-forget: не блокирует ответ API, если отправка зависнет/упадёт."""
    if not user_id:
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_send(user_id, text))
        else:
            loop.run_until_complete(_send(user_id, text))
    except RuntimeError:
        asyncio.run(_send(user_id, text))
