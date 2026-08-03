"""
Единая точка входа: запускает Telegram-бота и веб-сервер Mini App
в ОДНОМ процессе/контейнере — это гарантирует, что оба пишут и читают
одни и те же файлы данных (carwash_sessions.json и т.д.), то есть
касса, машины и мойщики синхронны между ботом и приложением без
дополнительной настройки (общий диск не нужен).

Команда запуска на Railway (Settings → Deploy → Custom Start Command):
    python run_all.py
"""
import os
import sys
import threading
import traceback

import uvicorn

from bot import main as bot_main
from sessions import load_sessions

print("🚀 run_all.py стартовал", flush=True)


def run_web():
    try:
        port = int(os.environ.get("PORT", 8000))
        print(f"🌐 Запускаю веб-сервер на 0.0.0.0:{port} ...", flush=True)
        uvicorn.run("webapp.server:app", host="0.0.0.0", port=port, log_level="info")
    except Exception:
        print("❌ ВЕБ-СЕРВЕР УПАЛ ПРИ СТАРТЕ:", flush=True)
        traceback.print_exc()
        sys.stdout.flush()


if __name__ == "__main__":
    load_sessions()  # грузим данные с диска в общую память ДО старта обоих сервисов
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    print("🌐 Веб-сервер (Mini App) запущен в фоновом потоке", flush=True)
    bot_main()  # блокирующий вызов — держит процесс живым
