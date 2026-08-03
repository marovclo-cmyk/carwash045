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
    session = get_session(branch)
    session["day_open"] = True
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


# ── ПРИВЯЗКА ПОЛЬЗОВАТЕЛЯ К ФИЛИАЛУ (на сегодняшнюю смену) ─────────────────
# Храним в user_data контекста telegram (per-chat), не здесь — см. handlers.
