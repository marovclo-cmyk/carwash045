# CarWash Mini App

## Что это
Telegram Mini App поверх твоего бота: касса, добавление машины, отчёты, мойщики.
Использует те же файлы данных (`carwash_sessions.json` и т.д.), что и сам бот —
никакой отдельной БД, всё синхронно.

## Установка
```
pip install fastapi uvicorn --break-system-packages
```

## Запуск локально (для теста)
```
cd Claude2.0
uvicorn webapp.server:app --reload --port 8000
```
Для теста в Telegram нужен HTTPS-туннель, например ngrok:
```
ngrok http 8000
```
Скопируй HTTPS-адрес (например `https://abcd1234.ngrok-free.app`).

## Продакшн-хостинг (бесплатные варианты)
Любой из них даёт HTTPS "из коробки":
- **Railway.app** — залить репозиторий, команда запуска `uvicorn webapp.server:app --host 0.0.0.0 --port $PORT`
- **Render.com** — Web Service, тот же старт-командой
- Requirements: `fastapi`, `uvicorn`, `python-telegram-bot`, всё что уже нужно боту

## Подключение к боту
1. В `.env` добавь:
   ```
   WEBAPP_URL=https://твой-адрес.com
   ```
2. Перезапусти бота.
3. В Telegram напиши боту `/app` — придёт кнопка "📱 Открыть приложение".

## Настройка через @BotFather (чтобы кнопка была в меню бота)
1. `/mybots` → выбери бота → `Bot Settings` → `Menu Button`
2. Укажи тот же `WEBAPP_URL`
3. Теперь синяя кнопка меню рядом с полем ввода тоже открывает приложение

## Структура
- `server.py` — FastAPI backend, дергает существующие `sessions.py`/`calculator.py`/`config.py`
- `static/index.html` — вся Mini App (одностраничная, без сборки, ванильный JS)

## Безопасность
`verify_init_data()` проверяет подпись Telegram `initData` через HMAC — это
гарантирует, что запрос реально пришёл из Telegram-клиента конкретного юзера,
а не подделан извне.
