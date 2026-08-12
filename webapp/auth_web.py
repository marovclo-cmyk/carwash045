"""
Веб-авторизация сайта (отдельно от Telegram initData).

Идея: один общий пароль на всю компанию (задаётся в .env как SITE_PASSWORD).
При входе человек вводит: пароль + своё имя + должность (роль):
    "мойщик" | "админ" | "владелец"

После успешного входа выдаётся токен (случайная строка), который хранится
в файле site_web_sessions.json (тот же DATA_DIR, что и остальные данные бота —
см. sessions.py). Токен живёт TOKEN_TTL секунд и передаётся сайтом в каждом
запросе заголовком:  X-Site-Token

Это НЕ заменяет Telegram-авторизацию бота — это отдельный, параллельный вход
для веб-версии. Данные (кассы, сотрудники и т.д.) общие — они читаются из
тех же JSON-файлов через sessions.py, поэтому изменения в боте сразу видны
на сайте и наоборот.

⚠️ Важно для продакшена:
- SITE_PASSWORD обязательно должен быть переопределён в .env (иначе используется
  дефолт "changeme", что небезопасно).
- Так как пароль общий на всех, разграничение по ролям на сайте — это разграничение
  "на доверии": сайт присваивает роль, которую человек выбрал при входе, и дальше
  бэкенд ограничивает действия по этой роли. Для более строгой защиты (нельзя же
  просто указать роль "владелец" зная общий пароль) см. TODO.md — пункт про
  привязку логина к конкретным ФИО из белого списка.
"""
import os
import secrets
import time
from typing import Optional

from fastapi import Header, HTTPException
from pydantic import BaseModel

from sessions import _read_json_locked, _write_json_locked, DATA_DIR  # переиспользуем ту же файловую блокировку

SITE_PASSWORD = os.getenv("SITE_PASSWORD", "changeme")
TOKEN_TTL = int(os.getenv("SITE_TOKEN_TTL", str(60 * 60 * 24 * 14)))  # 14 дней по умолчанию

SESSIONS_FILE = os.path.join(DATA_DIR, "site_web_sessions.json")

VALID_ROLES = ["мойщик", "админ", "владелец"]


class LoginIn(BaseModel):
    password: str
    name: str
    role: str
    branch: str = ""  # для мойщика/админа — филиал, к которому привязывается сессия


def _load() -> dict:
    return _read_json_locked(SESSIONS_FILE)


def _save(data: dict):
    _write_json_locked(SESSIONS_FILE, data)


def _cleanup(data: dict) -> dict:
    now = time.time()
    return {t: v for t, v in data.items() if v.get("expires", 0) > now}


def login(body: LoginIn) -> dict:
    if not secrets.compare_digest(body.password.strip(), SITE_PASSWORD):
        raise HTTPException(401, "Неверный пароль")
    name = body.name.strip()
    role = body.role.strip().lower()
    if not name:
        raise HTTPException(400, "Укажите имя")
    if role not in VALID_ROLES:
        raise HTTPException(400, f"Роль должна быть одной из: {', '.join(VALID_ROLES)}")
    if role != "владелец" and not body.branch:
        raise HTTPException(400, "Укажите филиал")

    token = secrets.token_urlsafe(32)
    data = _cleanup(_load())
    data[token] = {
        "name": name,
        "role": role,
        "branch": body.branch,
        "created": time.time(),
        "expires": time.time() + TOKEN_TTL,
    }
    _save(data)
    return {"token": token, "name": name, "role": role, "branch": body.branch}


def logout(token: str):
    data = _load()
    if token in data:
        del data[token]
        _save(data)


def get_session(token: str) -> Optional[dict]:
    if not token:
        return None
    data = _load()
    entry = data.get(token)
    if not entry:
        return None
    if entry.get("expires", 0) < time.time():
        return None
    return entry


def require_site_user(x_site_token: str = Header(default="")) -> dict:
    """Базовая зависимость: любой залогиненный (любая роль) пользователь сайта."""
    session = get_session(x_site_token)
    if not session:
        raise HTTPException(401, "Сессия истекла или не найдена, войдите заново")
    return session


def require_site_admin(x_site_token: str = Header(default="")) -> dict:
    """Роль admin или owner."""
    session = require_site_user(x_site_token)
    if session["role"] not in ("админ", "владелец"):
        raise HTTPException(403, "Нужны права администратора")
    return session


def require_site_owner(x_site_token: str = Header(default="")) -> dict:
    session = require_site_user(x_site_token)
    if session["role"] != "владелец":
        raise HTTPException(403, "Только для владельца")
    return session
