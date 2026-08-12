"""
Веб-авторизация сайта (отдельно от Telegram initData).

Идея (после GAP-S1, 09.08.2026): общий пароль на всю компанию (задаётся
в .env как SITE_PASSWORD) — это только первый фактор. Роль на сайте
БОЛЬШЕ НЕ выбирается пользователем свободно при входе — она определяется
системой по имени (ФИО), сверенному со списками, которые уже ведёт
владелец/админ через сайт:
    "мойщик" → sessions.get_branch_workers(branch)
    "админ"  → sessions.get_branch_admin_names(branch) (та же роспись
               «Администраторы», что и на панели дежурства в workers.html)
    "владелец" → отдельный allowlist имён SITE_OWNER_NAMES (см. ниже) —
               для роли владельца нет филиала и нет готового ФИО-списка
               в carwash_branches.json (владелец в системе один,
               определяется только по OWNER_ID в Telegram), поэтому это
               единственная новая сущность, добавленная в рамках GAP-S1.

При входе человек указывает: пароль + своё имя (+ филиал, если не
владелец). Если имени нет в соответствующем списке — вход отклоняется
(403), а не выдаётся роль "на веру", как было раньше.

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
- SITE_OWNER_NAMES обязательно должен быть задан в .env (список ФИО через
  запятую, например "Салим Иванов,Роман Петров") — без него НИКТО не
  сможет войти на сайт как владелец (осознанный fail-closed выбор — лучше
  временно потерять доступ владельцу и заметить проблему сразу при
  деплое, чем молча оставить лазейку для входа под чужой ролью).
- Роль "мойщик"/"админ" теперь определяется исключительно по спискам
  `workers`/`admin_names` конкретного филиала (`carwash_branches.json`) —
  теми же списками, что уже видны и редактируются на сайте (workers.html:
  панели «Мойщики»/«Администраторы»). Человек, которого владелец/админ
  не добавил в эти списки, не сможет войти на сайт под этим филиалом
  вообще — даже зная общий пароль.
"""
import os
import secrets
import time
from typing import Optional

from fastapi import Header, HTTPException
from pydantic import BaseModel

from sessions import (
    _read_json_locked, _write_json_locked, DATA_DIR,  # переиспользуем ту же файловую блокировку
    get_branch_workers, get_branch_admin_names,
)
from config import BRANCHES

SITE_PASSWORD = os.getenv("SITE_PASSWORD", "changeme")
TOKEN_TTL = int(os.getenv("SITE_TOKEN_TTL", str(60 * 60 * 24 * 14)))  # 14 дней по умолчанию

SESSIONS_FILE = os.path.join(DATA_DIR, "site_web_sessions.json")

# ФИО, которым разрешён вход на сайт как "владелец" — единственная новая
# сущность GAP-S1 (нет готового ФИО-списка владельцев в данных проекта,
# т.к. владелец определяется только по OWNER_ID в Telegram). Пусто по
# умолчанию → вход как владелец через сайт отключён, пока явно не задано.
SITE_OWNER_NAMES = [
    n.strip() for n in os.getenv("SITE_OWNER_NAMES", "").split(",") if n.strip()
]


class LoginIn(BaseModel):
    password: str
    name: str
    branch: str = ""  # пусто = попытка входа как владелец; иначе — филиал мойщика/админа


def _load() -> dict:
    return _read_json_locked(SESSIONS_FILE)


def _save(data: dict):
    _write_json_locked(SESSIONS_FILE, data)


def _cleanup(data: dict) -> dict:
    now = time.time()
    return {t: v for t, v in data.items() if v.get("expires", 0) > now}


def _match_name(name: str, roster: list[str]) -> Optional[str]:
    """Ищет имя в списке без учёта регистра/лишних пробелов. Возвращает
    каноническую запись из ростера (как она сохранена в конфиге), а не
    то, как ввёл пользователь — чтобы в сессии/логах было единое имя."""
    needle = name.strip().lower()
    for entry in roster:
        if entry.strip().lower() == needle:
            return entry
    return None


def _resolve_role(name: str, branch: str) -> tuple[str, str]:
    """Определяет роль и каноническое имя ПО ДАННЫМ СИСТЕМЫ, а не по тому,
    что человек выбрал в форме. Поднимает HTTPException, если имя не
    найдено ни в одном подходящем списке."""
    if not branch:
        matched = _match_name(name, SITE_OWNER_NAMES)
        if not matched:
            raise HTTPException(
                403,
                "Это имя не в списке владельцев. Если вы сотрудник филиала — "
                "укажите филиал вместо пустого поля.",
            )
        return "владелец", matched

    if branch not in BRANCHES:
        raise HTTPException(400, "Неизвестный филиал")

    matched = _match_name(name, get_branch_admin_names(branch))
    if matched:
        return "админ", matched

    matched = _match_name(name, get_branch_workers(branch))
    if matched:
        return "мойщик", matched

    raise HTTPException(
        403,
        f"«{name.strip()}» не числится в филиале «{branch}». Обратитесь к "
        "владельцу или администратору филиала, чтобы вас добавили в список "
        "(панель «Мойщики»/«Администраторы»).",
    )


def login(body: LoginIn) -> dict:
    # secrets.compare_digest() умеет сравнивать str только если ОБЕ строки
    # состоят исключительно из ASCII — иначе он бросает TypeError, а не
    # возвращает False. Пароль SITE_PASSWORD обычно ASCII, но ввод
    # пользователя — нет: опечатка на русской раскладке (кириллица вместо
    # латиницы — частый случай для этого продукта) роняла бы запрос в 500
    # вместо ожидаемого 401 "Неверный пароль". Сравниваем как bytes
    # (utf-8) — тогда не-ASCII ввод просто не совпадёт, как и должно быть.
    if not secrets.compare_digest(body.password.strip().encode(), SITE_PASSWORD.encode()):
        raise HTTPException(401, "Неверный пароль")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Укажите имя")
    branch = body.branch.strip()

    role, canonical_name = _resolve_role(name, branch)

    token = secrets.token_urlsafe(32)
    data = _cleanup(_load())
    data[token] = {
        "name": canonical_name,
        "role": role,
        "branch": branch,
        "created": time.time(),
        "expires": time.time() + TOKEN_TTL,
    }
    _save(data)
    return {"token": token, "name": canonical_name, "role": role, "branch": branch}


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
