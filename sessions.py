"""
Хранилище данных бота.

Ключевые изменения относительно версии "на пользователя":
- Касса (sessions) хранится по ФИЛИАЛУ, а не по user_id. Все, кто работает
  в одном филиале в этот день, видят и пишут в одну и ту же кассу.
- Запись в файлы атомарна (temp-файл + os.replace) и защищена файловой
  блокировкой (filelock), чтобы при параллельной работе нескольких
  сотрудников/филиалов не терялись и не портились данные.
- Список сотрудников и админ — атрибуты филиала (branches_config.json),
  а не личной сессии пользователя.
"""
import json
import os
import time
from datetime import datetime
from contextlib import contextmanager

from config import SALARY_ADMIN, BRANCHES, OWNER_ID

DATA_DIR = os.getenv("DATA_DIR", os.path.expanduser("~"))
os.makedirs(DATA_DIR, exist_ok=True)

SESSIONS_FILE = os.path.join(DATA_DIR, "carwash_sessions.json")
ARCHIVE_FILE  = os.path.join(DATA_DIR, "carwash_archive.json")
BRANCHES_FILE = os.path.join(DATA_DIR, "carwash_branches.json")
USERS_FILE    = os.path.join(DATA_DIR, "carwash_users.json")
CLIENTS_FILE  = os.path.join(DATA_DIR, "carwash_clients.json")
ADVANCES_FILE = os.path.join(DATA_DIR, "carwash_advances.json")
BOOKINGS_FILE = os.path.join(DATA_DIR, "carwash_bookings.json")

LOCK_TIMEOUT = 10  # секунд ожидания блокировки, прежде чем сдаться


class Timeout(Exception):
    pass


@contextmanager
def _file_lock(path: str, timeout: float = LOCK_TIMEOUT):
    """Простая межпроцессная блокировка на основе O_CREAT|O_EXCL.
    Не требует сторонних библиотек, работает на Linux/macOS из коробки.
    Если процесс упал и не снял лок (например kill -9), сторожевой
    таймаут по mtime лок-файла (LOCK_TIMEOUT*3) позволяет его "сорвать"."""
    lock_path = path + ".lock"
    deadline  = time.monotonic() + timeout
    fd = None
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.monotonic() - os.path.getmtime(lock_path)
            except OSError:
                age = 0
            if age > LOCK_TIMEOUT * 3:
                try:
                    os.remove(lock_path)
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise Timeout(f"Не удалось получить блокировку {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.remove(lock_path)
        except OSError:
            pass


def _lock(path: str):
    return _file_lock(path, LOCK_TIMEOUT)


def _atomic_write_json(path: str, data: dict):
    """Пишет JSON во временный файл и атомарно подменяет им целевой файл.
    Так файл никогда не остаётся в "битом" (наполовину записанном) виде,
    даже если процесс упадёт прямо во время записи."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _read_json_locked(path: str) -> dict:
    lock = _lock(path)
    try:
        with lock:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            return {}
    except Timeout:
        # Не удалось получить лок за разумное время — отдаём последнее
        # известное состояние из памяти, чтобы бот не падал.
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json_locked(path: str, data: dict):
    lock = _lock(path)
    try:
        with lock:
            _atomic_write_json(path, data)
    except Timeout:
        print(f"⚠️ Не удалось получить блокировку на {path} за {LOCK_TIMEOUT}с")


def _update_json_locked(path: str, update_fn):
    """Атомарно: читает файл, применяет update_fn(data) -> data, пишет обратно.
    Вся операция (чтение+изменение+запись) происходит под одной блокировкой,
    что устраняет гонки между параллельными запросами разных пользователей."""
    lock = _lock(path)
    try:
        with lock:
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    data = {}
            else:
                data = {}
            data = update_fn(data)
            _atomic_write_json(path, data)
            return data
    except Timeout:
        print(f"⚠️ Не удалось получить блокировку на {path} за {LOCK_TIMEOUT}с")
        return None


# ── СЕССИИ (КАССА ПО ФИЛИАЛУ) ───────────────────────────────────────────────
# В памяти процесса держим кэш — большинство чтений идёт отсюда,
# а на диск пишем через _update_json_locked при каждом изменении.

sessions: dict[str, dict] = {}   # branch -> session


def load_sessions():
    global sessions
    sessions = _read_json_locked(SESSIONS_FILE)


def save_sessions():
    """Сбрасывает весь текущий кэш sessions на диск под блокировкой.
    Используется после прямого изменения sessions[branch] в памяти."""
    def _update(_old):
        return sessions
    _update_json_locked(SESSIONS_FILE, _update)


def get_session(branch: str) -> dict:
    if not branch:
        # Подстраховка: если пользователь ещё не выбрал филиал /newday,
        # не должно дойти до сюда — но на всякий случай не падаем.
        branch = "—"
    if branch not in sessions:
        sessions[branch] = _empty_session(branch)
        save_sessions()
    s = sessions[branch]
    for key in ("loyalty", "expenses", "incomes", "cars", "products"):
        if key not in s:
            s[key] = []
    if "admin_name" not in s:
        s["admin_name"] = ""
    if "day_open" not in s:
        # Обратная совместимость: у уже идущих смен (в которых уже есть
        # данные) не должно внезапно заблокироваться добавление машин —
        # считаем их уже открытыми. Действительно новые/пустые смены
        # остаются закрытыми, пока админ явно не нажмёт «Открыть смену».
        s["day_open"] = session_has_data(s)
    return s


def open_day(branch: str):
    """Открывает смену филиала. Заодно подтягивает дату смены к сегодняшней —
    иначе, если «Открыть смену» нажали без предварительного /newday
    (закрытия предыдущей смены), в session остаётся дата вчерашней/более
    старой смены, и всё, что сверяется с «сегодня» (например, привязка
    записи из booking к машине в кассе), ошибочно считает текущую смену
    «не сегодняшней»."""
    session = get_session(branch)
    session["day_open"] = True
    session["date"] = datetime.now().strftime("%d.%m.%Y")
    save_sessions()


def reset_session(branch: str):
    sessions[branch] = _empty_session(branch)
    save_sessions()


def _empty_session(branch: str) -> dict:
    return {
        "date":          datetime.now().strftime("%d.%m.%Y"),
        "branch":        branch,
        "cars":          [],
        "products":      [],
        "expenses":      [],
        "incomes":       [],
        "loyalty":       [],
        "admin_percent": SALARY_ADMIN,
        "admin_name":    "",
        "day_open":      False,
    }


def session_has_data(session: dict) -> bool:
    """Есть ли в смене хоть что-то, что стоит сохранить/показать — не только
    машины. Пустой отчёт (ни одной машины) всё равно "не пустой", если
    сотруднику или администратору проставлена фиксированная ставка — иначе
    эта ставка молча терялась бы при старте нового дня или не давала бы
    закрыть/посмотреть отчёт."""
    return bool(
        session.get("cars") or session.get("products") or
        session.get("expenses") or session.get("incomes") or
        session.get("fixed_rates") or session.get("admin_fixed_rate")
    )


# ── АРХИВ ────────────────────────────────────────────────────────────────────

def load_archive() -> dict:
    return _read_json_locked(ARCHIVE_FILE)


def save_to_archive(branch: str, session: dict):
    date = session.get("date", datetime.now().strftime("%d.%m.%Y"))

    def _update(archive):
        archive.setdefault(branch, {})[date] = {
            "date":          date,
            "branch":        branch,
            "cars":          session.get("cars", []),
            "products":      session.get("products", []),
            "expenses":      session.get("expenses", []),
            "incomes":       session.get("incomes", []),
            "loyalty":       session.get("loyalty", []),
            "admin_percent": session.get("admin_percent", SALARY_ADMIN),
            "admin_name":    session.get("admin_name", ""),
            "fixed_rates":       session.get("fixed_rates", {}),
            "admin_fixed_rate":  session.get("admin_fixed_rate", 0),
        }
        return archive

    _update_json_locked(ARCHIVE_FILE, _update)


def overwrite_archive_day(branch: str, date: str, day: dict):
    """Полностью заменяет запись конкретного дня в архиве конкретного
    филиала. Используется для ручного исправления испорченных дней
    (например, если день случайно переоткрылся и в него дописались
    машины из другого дня)."""
    def _update(archive):
        archive.setdefault(branch, {})[date] = day
        return archive
    _update_json_locked(ARCHIVE_FILE, _update)


def set_archive_admin_name(branch: str, date: str, name: str) -> bool:
    """Задним числом проставить, кто дежурил администратором в уже
    архивированный день (нужно для истории зарплаты — раньше это поле
    не сохранялось). Возвращает False, если такого дня нет в архиве."""
    result = {"ok": False}

    def _update(archive):
        day = archive.get(branch, {}).get(date)
        if day is None:
            result["ok"] = False
            return archive
        day["admin_name"] = name
        result["ok"] = True
        return archive

    _update_json_locked(ARCHIVE_FILE, _update)
    return result["ok"]


def patch_fixed_rates(day: dict, rate_updates: dict, admin_amount: int | None = None) -> None:
    """Задним числом добавляет/меняет фикс-ставки (мойщика и/или админа)
    прямо в словаре дня — общая логика для архивного дня и текущей смены.
    amount <= 0 у конкретного сотрудника удаляет его ставку."""
    day.setdefault("fixed_rates", {})
    for name, amount in rate_updates.items():
        if amount <= 0:
            day["fixed_rates"].pop(name, None)
        else:
            day["fixed_rates"][name] = amount
    if admin_amount is not None:
        if admin_amount <= 0:
            day.pop("admin_fixed_rate", None)
        else:
            day["admin_fixed_rate"] = admin_amount


def patch_archive_fixed_rates(branch: str, date: str, rate_updates: dict, admin_amount: int | None = None,
                               create_if_missing: bool = False, admin_name: str = "") -> bool:
    """Задним числом проставить фикс-ставки в архивный день. Если дня ещё
    нет в архиве (например, за этот день вообще ничего не заводили — ни
    одной машины) и create_if_missing=True — создаёт ПУСТОЙ день (0 машин,
    0 касса) и сразу проставляет туда ставки, то есть день перестаёт быть
    "пустым": в нём остаётся ставка каждого сотрудника. Возвращает False,
    только если create_if_missing=False и такого дня нет в архиве."""
    result = {"ok": False}

    def _update(archive):
        branch_archive = archive.setdefault(branch, {})
        day = branch_archive.get(date)
        if day is None:
            if not create_if_missing:
                result["ok"] = False
                return archive
            day = {
                "date": date, "branch": branch,
                "cars": [], "products": [], "expenses": [], "incomes": [], "loyalty": [],
                "admin_percent": SALARY_ADMIN, "admin_name": admin_name,
            }
            branch_archive[date] = day
        patch_fixed_rates(day, rate_updates, admin_amount)
        result["ok"] = True
        return archive

    _update_json_locked(ARCHIVE_FILE, _update)
    return result["ok"]


# ── КОНФИГ ФИЛИАЛОВ: админ + сотрудники ─────────────────────────────────────
# branches_config.json: { branch: {"admin": user_id|0, "workers": [str, ...]} }

_branches_cache: dict[str, dict] | None = None


def _default_branches_config() -> dict:
    return {b: {"admin": 0, "workers": [], "admin_names": []} for b in BRANCHES}


def load_branches_config() -> dict:
    global _branches_cache
    data = _read_json_locked(BRANCHES_FILE)
    if not data:
        data = _default_branches_config()
        _write_json_locked(BRANCHES_FILE, data)
    # миграция — гарантируем наличие всех текущих филиалов и нужных ключей
    changed = False
    for b in BRANCHES:
        if b not in data:
            data[b] = {"admin": 0, "workers": []}
            changed = True
    for b, cfg in data.items():
        if "admin" not in cfg:
            cfg["admin"] = 0; changed = True
        if "workers" not in cfg:
            cfg["workers"] = []; changed = True
        if "admin_names" not in cfg:
            cfg["admin_names"] = []; changed = True
    if changed:
        _write_json_locked(BRANCHES_FILE, data)
    _branches_cache = data
    return data


def get_branch_config(branch: str) -> dict:
    cfg = _branches_cache or load_branches_config()
    return cfg.get(branch, {"admin": 0, "workers": [], "admin_names": []})


def get_branch_admin(branch: str) -> int:
    return get_branch_config(branch).get("admin", 0)


def get_branch_admin_name(branch: str) -> str:
    """Имя назначенного админа филиала (для PDF/отчётов). Если не назначен — 'Салим' (админ по умолчанию)."""
    admin_id = get_branch_admin(branch)
    if not admin_id:
        return "Салим"
    users = load_users()
    return users.get(str(admin_id), users.get(admin_id, "Салим"))


def is_branch_admin(user_id: int, branch: str) -> bool:
    """Владелец (OWNER_ID) — админ всех филиалов.
    user_id обязателен и не может быть 0/пустым — иначе не назначенный
    admin (0 по умолчанию в branches_config.json) случайно совпадёт
    с неопознанным пользователем (0) и даст ему права админа."""
    if not user_id:
        return False
    if user_id == OWNER_ID:
        return True
    branch_admin = get_branch_admin(branch)
    return bool(branch_admin) and branch_admin == user_id


def is_branch_worker(user_id: int, branch: str) -> bool:
    """Мойщик ли этот пользователь ИМЕННО в этом филиале (сверяем его имя
    из белого списка со списком сотрудников филиала)."""
    if not user_id or not branch:
        return False
    users = load_users()
    name = users.get(str(user_id))
    if not name:
        return False
    return name in get_branch_workers(branch)


def get_role(user_id: int, branch: str | None) -> str:
    """Роль пользователя СТРОГО для конкретного филиала: 'owner' / 'admin' /
    'worker'. По умолчанию (нет данных, филиал не указан, пользователь не
    числится админом/сотрудником именно этого филиала) — 'worker', то есть
    минимальные права. Роль никогда не "утекает" с одного филиала на другой."""
    if user_id == OWNER_ID:
        return "owner"
    if branch and is_branch_admin(user_id, branch):
        return "admin"
    return "worker"


def set_branch_admin(branch: str, user_id: int):
    def _update(data):
        data.setdefault(branch, {"admin": 0, "workers": []})
        data[branch]["admin"] = user_id
        return data
    global _branches_cache
    _branches_cache = _update_json_locked(BRANCHES_FILE, _update)


def get_branch_workers(branch: str) -> list[str]:
    return get_branch_config(branch).get("workers", [])


def add_branch_worker(branch: str, name: str) -> bool:
    """Возвращает False, если сотрудник уже есть."""
    result = {"added": False}

    def _update(data):
        data.setdefault(branch, {"admin": 0, "workers": []})
        workers = data[branch].setdefault("workers", [])
        if name in workers:
            result["added"] = False
        else:
            workers.append(name)
            result["added"] = True
        return data

    global _branches_cache
    _branches_cache = _update_json_locked(BRANCHES_FILE, _update)
    return result["added"]


def remove_branch_worker(branch: str, name: str) -> bool:
    result = {"removed": False}

    def _update(data):
        data.setdefault(branch, {"admin": 0, "workers": []})
        workers = data[branch].setdefault("workers", [])
        if name in workers:
            workers.remove(name)
            result["removed"] = True
        return data

    global _branches_cache
    _branches_cache = _update_json_locked(BRANCHES_FILE, _update)
    return result["removed"]


# ── РОСТЕР АДМИНИСТРАТОРОВ ФИЛИАЛА (имена, без привязки к Telegram) ────────
# В отличие от get_branch_admin/set_branch_admin (один Telegram user_id,
# управляет правами доступа в БОТЕ), это — список ИМЁН администраторов
# филиала для сайта: несколько человек может числиться админами одного
# филиала (например, посменно), а какой из них "дежурит сегодня" —
# отдельное поле сессии (см. get_session_admin_name/set_session_admin_name).

def get_branch_admin_names(branch: str) -> list[str]:
    return get_branch_config(branch).get("admin_names", [])


def add_branch_admin_name(branch: str, name: str) -> bool:
    """Возвращает False, если такой админ уже есть."""
    result = {"added": False}

    def _update(data):
        data.setdefault(branch, {"admin": 0, "workers": [], "admin_names": []})
        names = data[branch].setdefault("admin_names", [])
        if name in names:
            result["added"] = False
        else:
            names.append(name)
            result["added"] = True
        return data

    global _branches_cache
    _branches_cache = _update_json_locked(BRANCHES_FILE, _update)
    return result["added"]


def remove_branch_admin_name(branch: str, name: str) -> bool:
    result = {"removed": False}

    def _update(data):
        data.setdefault(branch, {"admin": 0, "workers": [], "admin_names": []})
        names = data[branch].setdefault("admin_names", [])
        if name in names:
            names.remove(name)
            result["removed"] = True
        return data

    global _branches_cache
    _branches_cache = _update_json_locked(BRANCHES_FILE, _update)
    return result["removed"]


def get_session_admin_name(branch: str) -> str:
    """Кто из ростера администраторов дежурит СЕГОДНЯ (в текущей смене)."""
    return get_session(branch).get("admin_name", "")


def set_session_admin_name(branch: str, name: str):
    session = get_session(branch)
    session["admin_name"] = name
    save_sessions()


# ── ГРАФИК РАБОТЫ МОЙЩИКОВ (например 3/1 — 3 дня работает, 1 отдыхает) ──────

def set_worker_schedule(branch: str, name: str, work_days: int, rest_days: int, start_date: str):
    """start_date в формате YYYY-MM-DD — точка отсчёта цикла."""
    def _update(data):
        data.setdefault(branch, {"admin": 0, "workers": []})
        schedules = data[branch].setdefault("schedules", {})
        schedules[name] = {"work": work_days, "rest": rest_days, "start": start_date}
        return data

    global _branches_cache
    _branches_cache = _update_json_locked(BRANCHES_FILE, _update)


def clear_worker_schedule(branch: str, name: str) -> bool:
    result = {"removed": False}

    def _update(data):
        data.setdefault(branch, {"admin": 0, "workers": []})
        schedules = data[branch].setdefault("schedules", {})
        if name in schedules:
            del schedules[name]
            result["removed"] = True
        return data

    global _branches_cache
    _branches_cache = _update_json_locked(BRANCHES_FILE, _update)
    return result["removed"]


def get_worker_schedule(branch: str, name: str) -> dict | None:
    return get_branch_config(branch).get("schedules", {}).get(name)


def is_working_on(branch: str, name: str, on_date=None) -> bool:
    """Работает ли мойщик в указанный день согласно графику.
    Если график не задан — считаем, что мойщик доступен всегда (True)."""
    from datetime import date as _date
    sched = get_worker_schedule(branch, name)
    if not sched:
        return True
    try:
        start = _date.fromisoformat(sched["start"])
    except (ValueError, KeyError):
        return True
    on_date = on_date or _date.today()
    cycle = sched["work"] + sched["rest"]
    if cycle <= 0:
        return True
    days_passed = (on_date - start).days
    # % в Python корректно работает и для отрицательных чисел (цикл продолжается
    # «назад» по времени так же регулярно, как и вперёд) — это и нужно для
    # отображения недели, в которую может попадать дата раньше start_date.
    return (days_passed % cycle) < sched["work"]


def get_schedule_status(branch: str) -> dict:
    """{worker: {'working': bool, 'schedule': {...} | None}} на сегодня."""
    workers = get_branch_workers(branch)
    return {
        w: {"working": is_working_on(branch, w), "schedule": get_worker_schedule(branch, w)}
        for w in workers
    }


# ── ПОЛЬЗОВАТЕЛИ (белый список) ─────────────────────────────────────────────

def load_users() -> dict:
    return _read_json_locked(USERS_FILE)


def save_users(users: dict):
    _write_json_locked(USERS_FILE, users)


def add_user(user_id: int, name: str):
    def _update(data):
        data[str(user_id)] = name
        return data
    _update_json_locked(USERS_FILE, _update)


def remove_user(user_id: int) -> bool:
    result = {"removed": False}

    def _update(data):
        if str(user_id) in data:
            data.pop(str(user_id))
            result["removed"] = True
        return data

    _update_json_locked(USERS_FILE, _update)
    return result["removed"]


# ── КЛИЕНТЫ (карточка клиента, история визитов, поиск) ─────────────────────
# carwash_clients.json: { normalized_phone: {"phone","name","cars":[...],
#                          "visits":[{"date","branch","car","total","car_num"}]} }
# Клиенты общие на всю сеть — один и тот же человек может приехать в разный
# филиал, это один и тот же клиент. total_spent/visit_count не хранятся,
# а считаются из visits на лету — чтобы не рассинхронизировались, если
# машину потом отредактируют/удалят (это уже не откатывается автоматически,
# но зато исходные данные всегда согласованы сами с собой).

def normalize_phone(phone: str) -> str:
    """Оставляет только цифры; российский номер с ведущей 8 приводит к 7,
    чтобы 89991234567 и 79991234567 считались одним и тем же клиентом."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    return digits


def load_clients() -> dict:
    return _read_json_locked(CLIENTS_FILE)


def find_client(phone: str) -> dict | None:
    phone = normalize_phone(phone)
    if not phone:
        return None
    client = load_clients().get(phone)
    return client_summary(client) if client else None


def search_clients(query: str, limit: int = 8) -> list[dict]:
    """Ищет клиентов по подстроке телефона ИЛИ имени (регистронезависимо).
    Используется для автодополнения на сайте/в mini-app/в боте."""
    query = (query or "").strip()
    if not query:
        return []
    q_digits = "".join(ch for ch in query if ch.isdigit())
    q_lower  = query.lower()
    out = []
    for phone, client in load_clients().items():
        match = False
        if q_digits and q_digits in phone:
            match = True
        if not match and q_lower and q_lower in (client.get("name") or "").lower():
            match = True
        if match:
            out.append(client_summary(client))
    # Сначала точные совпадения по началу телефона/имени — удобнее при наборе.
    out.sort(key=lambda c: not (c["phone"].startswith(q_digits) if q_digits
              else (c.get("name") or "").lower().startswith(q_lower)))
    return out[:limit]


def client_summary(client: dict) -> dict:
    """Добавляет вычисляемые поля (визитов, всего потрачено, последний визит)
    поверх сырой записи клиента. discount_percent прокидывается как есть —
    его отсутствие в client означает "скидка не установлена"."""
    visits = client.get("visits", [])
    return {
        **client,
        "visit_count": len(visits),
        "total_spent": sum(v.get("total", 0) for v in visits),
        "last_visit": visits[-1]["date"] if visits else None,
        "discount_percent": client.get("discount_percent"),
    }


def set_client_discount(phone: str, percent: float) -> dict | None:
    """Устанавливает постоянную скидку клиента (0 < percent <= 100).
    Возвращает обновлённую карточку или None, если клиента с таким
    телефоном нет."""
    phone = normalize_phone(phone)
    if not phone:
        return None
    result = {}

    def _update(data):
        client = data.get(phone)
        if client is None:
            result["client"] = None
            return data
        client["discount_percent"] = percent
        result["client"] = client
        return data

    _update_json_locked(CLIENTS_FILE, _update)
    client = result.get("client")
    return client_summary(client) if client else None


def clear_client_discount(phone: str) -> dict | None:
    """Снимает постоянную скидку клиента (убирает поле полностью, а не
    просто зануляет — чтобы отличать "скидка 0%" от "скидка не задана",
    хотя первое сейчас нигде не создаётся)."""
    phone = normalize_phone(phone)
    if not phone:
        return None
    result = {}

    def _update(data):
        client = data.get(phone)
        if client is None:
            result["client"] = None
            return data
        client.pop("discount_percent", None)
        result["client"] = client
        return data

    _update_json_locked(CLIENTS_FILE, _update)
    client = result.get("client")
    return client_summary(client) if client else None


def import_contact_car_labels(entries: list[tuple[str, str]]) -> dict:
    """Массово подгружает список (телефон, ярлык) из контактов телефона —
    например экспорт из iCloud. Ярлык там обычно НЕ имя человека, а название/
    номер машины (так исторически сохранялись контакты — «Мазда», «Соляра
    538»...), поэтому он кладётся в список машин клиента (cars), а НЕ в имя.
    Имя клиента остаётся пустым, пока не будет реально указано (при следующей
    мойке через карточку клиента или вручную). Если клиент с таким телефоном
    уже есть — ярлык лишь добавляется в его cars (если там ещё нет), имя и
    визиты не трогаются. Возвращает {"added_new", "updated_existing", "skipped_invalid"}."""
    added_new = 0
    updated_existing = 0
    skipped_invalid = 0

    def _update(data):
        nonlocal added_new, updated_existing, skipped_invalid
        for phone, label in entries:
            phone = normalize_phone(phone)
            label = (label or "").strip()
            if not phone:
                skipped_invalid += 1
                continue
            if phone not in data:
                data[phone] = {"phone": phone, "name": "", "cars": [label] if label else [], "visits": []}
                added_new += 1
            else:
                cars = data[phone].setdefault("cars", [])
                if label and label not in cars:
                    cars.append(label)
                    updated_existing += 1
        return data

    _update_json_locked(CLIENTS_FILE, _update)
    return {"added_new": added_new, "updated_existing": updated_existing, "skipped_invalid": skipped_invalid}


def fix_imported_contact_names(entries: list[tuple[str, str]]) -> dict:
    """Разовое исправление прошлой ошибки: ярлык из контактов (название машины,
    а не имя человека) раньше по ошибке сохранялся прямо в поле name клиента.
    Если текущее имя клиента ТОЧНО совпадает с этим ярлыком (значит, его никто
    вручную не менял после того импорта) — переносит ярлык в cars и очищает
    name, чтобы карточка честно показывала «Без имени» вместо названия машины."""
    fixed = 0

    def _update(data):
        nonlocal fixed
        by_phone = {}
        for phone, label in entries:
            p = normalize_phone(phone)
            if p:
                by_phone[p] = (label or "").strip()
        for phone, client in data.items():
            label = by_phone.get(phone)
            if not label:
                continue
            if client.get("name") == label:
                client["name"] = ""
                cars = client.setdefault("cars", [])
                if label not in cars:
                    cars.append(label)
                fixed += 1
        return data

    _update_json_locked(CLIENTS_FILE, _update)
    return {"fixed": fixed}


def update_client(phone: str, name: str | None = None, cars: list[str] | None = None) -> dict | None:
    """Точечное обновление карточки клиента (имя и/или список машин), без
    добавления визита — используется при ручном редактировании на вкладке
    «Клиенты» и при простановке имени клиенту, у которого телефон уже был
    указан ранее. Возвращает обновлённую карточку или None, если клиента
    с таким телефоном нет."""
    phone = normalize_phone(phone)
    if not phone:
        return None
    result = {}

    def _update(data):
        client = data.get(phone)
        if client is None:
            result["client"] = None
            return data
        if name is not None:
            client["name"] = name.strip()
        if cars is not None:
            client["cars"] = cars
        result["client"] = client
        return data

    _update_json_locked(CLIENTS_FILE, _update)
    client = result.get("client")
    return client_summary(client) if client else None


def upsert_client_visit(phone: str, name: str, branch: str, car: str,
                         total: int, car_num: int | None = None,
                         date: str | None = None, service: str = "",
                         time: str = "", paid: int | None = None,
                         status: str = "done") -> dict:
    """Заводит клиента (если новый) или обновляет карточку и добавляет визит.
    Возвращает актуальную карточку клиента (с вычисляемыми полями).

    service/time/paid/status — доп. поля для отображения визита в духе
    макета (История посещений): состав услуг, время, сколько реально
    оплачено и статус. Необязательные — старые вызовы без них по-прежнему
    работают, просто визит будет чуть более "голым" в выдаче."""
    phone = normalize_phone(phone)
    date = date or datetime.now().strftime("%d.%m.%Y")
    if paid is None:
        paid = total  # запись в кассу = деньги уже приняты
    result = {}

    def _update(data):
        client = data.setdefault(phone, {"phone": phone, "name": "", "cars": [], "visits": []})
        if name:
            client["name"] = name
        if car and car not in client["cars"]:
            client["cars"].append(car)
        client["visits"].append({
            "date": date, "branch": branch, "car": car,
            "total": total, "car_num": car_num,
            "service": service, "time": time, "paid": paid, "status": status,
        })
        result["client"] = client
        return data

    _update_json_locked(CLIENTS_FILE, _update)
    return client_summary(result["client"])


# ── АВАНСЫ СОТРУДНИКОВ ──────────────────────────────────────────────────
# carwash_advances.json: { branch: { name: [ {"idx","date","amount","ts"} ] } }
# Аванс не привязан к дневной кассе — выдаётся "здесь и сейчас" админом
# филиала и вычитается из недельного/месячного заработка сотрудника
# (см. employee_period_stats в employee_stats.py).

def add_advance(branch: str, name: str, amount: int) -> dict:
    """Записывает выдачу аванса. Возвращает добавленную запись."""
    result = {}

    def _update(data):
        branch_data = data.setdefault(branch, {})
        entries = branch_data.setdefault(name, [])
        idx = (max((e.get("idx", -1) for e in entries), default=-1) + 1)
        entry = {
            "idx": idx,
            "date": datetime.now().strftime("%d.%m.%Y"),
            "amount": amount,
            "ts": time.time(),
        }
        entries.append(entry)
        result["entry"] = entry
        return data

    _update_json_locked(ADVANCES_FILE, _update)
    return result["entry"]


def get_employee_advances(branch: str, name: str,
                           date_from: datetime | None = None,
                           date_to: datetime | None = None) -> list[dict]:
    """Список авансов сотрудника, опционально отфильтрованный по датам
    (date_from/date_to — datetime, включительно). Без фильтра — все авансы."""
    data = _read_json_locked(ADVANCES_FILE)
    entries = data.get(branch, {}).get(name, [])
    if date_from is None and date_to is None:
        return list(entries)
    out = []
    for e in entries:
        try:
            d = datetime.strptime(e["date"], "%d.%m.%Y")
        except (ValueError, TypeError, KeyError):
            continue
        if date_from is not None and d < date_from.replace(hour=0, minute=0, second=0, microsecond=0):
            continue
        if date_to is not None and d > date_to.replace(hour=23, minute=59, second=59, microsecond=999999):
            continue
        out.append(e)
    return out


def delete_advance(branch: str, name: str, idx: int) -> bool:
    """Удаляет запись об авансе по её idx. True, если запись была найдена и удалена."""
    result = {"removed": False}

    def _update(data):
        entries = data.get(branch, {}).get(name, [])
        for i, e in enumerate(entries):
            if e.get("idx") == idx:
                entries.pop(i)
                result["removed"] = True
                break
        return data

    _update_json_locked(ADVANCES_FILE, _update)
    return result["removed"]


# ── ЗАПИСИ (ЖУРНАЛ ЗАПИСИ / BOOKINGS) ───────────────────────────────────────
# carwash_bookings.json: { branch: { "ДД.ММ.ГГГГ": [ {запись}, ... ] } }
# Запись — это будущий/сегодняшний слот в боксе (в отличие от "машины" в
# sessions/cars, которая появляется в кассе по факту приезда клиента).
# id записи уникален глобально по всему файлу (как и car.num — но
# car.num уникален только в рамках одной смены филиала, а запись должна
# однозначно адресоваться без указания филиала/даты — отсюда сквозной id).
#
# Статусы записи (BOOKING_STATUSES зеркалируется в webapp/server.py):
#   waiting     — ожидание (по умолчанию, только создана)
#   confirmed   — клиент подтвердил, что приедет
#   arrived     — клиент приехал, машина в боксе
#   no_show     — клиент не пришёл (бокс/время считаются снова свободными)
#   in_progress — мойка в процессе
#   done        — оплачено/завершено
#
# Бокс (box) — просто порядковый номер (1..N). Привязка "бокс = сотрудник
# по порядку в списке сотрудников филиала" делается через get_branch_boxes();
# при этом у самой записи хранится ещё и employee (снэпшот имени на момент
# создания/редактирования записи), т.к. состав сотрудников филиала может
# со временем меняться, а исторические записи должны показывать того, кто
# реально был назначен.

def load_bookings() -> dict:
    return _read_json_locked(BOOKINGS_FILE)


def get_bookings(branch: str, date: str) -> list[dict]:
    """Все записи филиала на конкретную дату (ДД.ММ.ГГГГ), в порядке создания."""
    return load_bookings().get(branch, {}).get(date, [])


def get_branch_boxes(branch: str, on_date=None) -> list[dict]:
    """Боксы филиала = сотрудники филиала по порядку, пронумерованные с 1.
    Пока в проекте нет отдельной сущности "бокс" — по умолчанию бокс #N
    соответствует N-му сотруднику в списке (см. 00-audit-i-plan.md, п.1).

    on_date (datetime.date | None) — если передана, у каждого бокса
    дополнительно считается on_duty: работает ли сотрудник в этот день
    по графику (is_working_on). Нужно странице «Запись», чтобы можно было
    показывать только тех, кто реально на смене в выбранный день, а не
    всех сотрудников филиала подряд. Если график не задан — считается,
    что сотрудник доступен всегда (см. is_working_on)."""
    workers = get_branch_workers(branch)
    return [
        {"box": i + 1, "employee": name, "on_duty": is_working_on(branch, name, on_date)}
        for i, name in enumerate(workers)
    ]


def _time_to_minutes(value: str) -> int:
    try:
        h, m = value.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return -1


def find_conflicting_booking(branch: str, date: str, box: int, start_time: str, end_time: str,
                              exclude_id: int | None = None) -> dict | None:
    """Ищет запись в том же боксе/дате, чей интервал пересекается с
    [start_time, end_time). Записи со статусом no_show не считаются
    занятыми — слот, на который клиент не пришёл, снова свободен."""
    new_start, new_end = _time_to_minutes(start_time), _time_to_minutes(end_time)
    for b in get_bookings(branch, date):
        if b.get("box") != box or b.get("id") == exclude_id or b.get("status") == "no_show":
            continue
        ex_start, ex_end = _time_to_minutes(b.get("start_time", "")), _time_to_minutes(b.get("end_time", ""))
        if new_start < ex_end and ex_start < new_end:
            return b
    return None


def _find_booking(data: dict, booking_id: int):
    """Ищет запись по id по всему файлу. Возвращает (branch, date, booking) или None."""
    for branch, days in data.items():
        for date, items in days.items():
            for b in items:
                if b.get("id") == booking_id:
                    return branch, date, b
    return None


def get_booking(booking_id: int) -> dict | None:
    found = _find_booking(load_bookings(), booking_id)
    return found[2] if found else None


def create_booking(branch: str, date: str, box: int, start_time: str, end_time: str,
                    employee: str = "", body_type: str = "", car: str = "",
                    service_keys: list[str] | None = None, custom_services: list[dict] | None = None,
                    product_keys: list[str] | None = None, price: int = 0, price_calc: int = 0,
                    price_override: int | None = None, payment: str = "",
                    payment_split: dict | None = None, comment: str = "",
                    phone: str = "", client_name: str = "", status: str = "waiting") -> dict:
    """Создаёт запись и возвращает её. id выдаётся сквозным счётчиком
    (максимум существующих id + 1) под той же блокировкой, что и запись —
    чтобы параллельные создания не получили одинаковый id."""
    result = {}

    def _update(data):
        max_id = 0
        for days in data.values():
            for items in days.values():
                for b in items:
                    max_id = max(max_id, b.get("id", 0))
        booking = {
            "id": max_id + 1,
            "branch": branch,
            "date": date,
            "box": box,
            "start_time": start_time,
            "end_time": end_time,
            "employee": employee,
            "body_type": body_type,
            "car": car,
            "service_keys": service_keys or [],
            "custom_services": custom_services or [],
            "product_keys": product_keys or [],
            "price": price,
            "price_calc": price_calc,
            "price_override": price_override,
            "payment": payment,
            "payment_split": payment_split,
            "comment": comment,
            "phone": normalize_phone(phone) if phone else "",
            "client_name": client_name,
            "status": status,
            "car_num": None,   # номер машины в кассе смены, если запись уже конвертирована (статус arrived)
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        data.setdefault(branch, {}).setdefault(date, []).append(booking)
        result["booking"] = booking
        return data

    _update_json_locked(BOOKINGS_FILE, _update)
    return result["booking"]


def update_booking(booking_id: int, **fields) -> dict | None:
    """Точечное обновление записи по id. Поддерживает перенос записи на
    другую дату/филиал/бокс — в этом случае запись переносится в другой
    список внутри того же файла. Ключи в fields со значением None
    игнорируются (кроме служебных price_override/comment/phone — вызывающий
    код должен передавать только реально изменяемые поля)."""
    result = {"booking": None}

    def _update(data):
        found = _find_booking(data, booking_id)
        if not found:
            return data
        branch, date, booking = found
        new_branch = fields.pop("branch", None) or branch
        new_date = fields.pop("date", None) or date
        for k, v in fields.items():
            if v is not None:
                booking[k] = v
        booking["updated_at"] = datetime.now().isoformat(timespec="seconds")
        if new_branch != branch or new_date != date:
            data[branch][date] = [b for b in data[branch][date] if b.get("id") != booking_id]
            if not data[branch][date]:
                del data[branch][date]
            if not data[branch]:
                del data[branch]
            booking["branch"] = new_branch
            booking["date"] = new_date
            data.setdefault(new_branch, {}).setdefault(new_date, []).append(booking)
        result["booking"] = booking
        return data

    _update_json_locked(BOOKINGS_FILE, _update)
    return result["booking"]


def set_booking_status(booking_id: int, status: str) -> dict | None:
    return update_booking(booking_id, status=status)


def delete_booking(booking_id: int) -> bool:
    result = {"deleted": False}

    def _update(data):
        found = _find_booking(data, booking_id)
        if not found:
            return data
        branch, date, booking = found
        data[branch][date] = [b for b in data[branch][date] if b.get("id") != booking_id]
        if not data[branch][date]:
            del data[branch][date]
        if not data[branch]:
            del data[branch]
        result["deleted"] = True
        return data

    _update_json_locked(BOOKINGS_FILE, _update)
    return result["deleted"]


# ── ПРИВЯЗКА ПОЛЬЗОВАТЕЛЯ К ФИЛИАЛУ (на сегодняшнюю смену) ─────────────────
# Храним в user_data контекста telegram (per-chat), не здесь — см. handlers.
