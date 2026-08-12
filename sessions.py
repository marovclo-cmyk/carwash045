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
from payment_provider import get_provider, is_mock_active, PaymentProviderError
from db import get_db_session
from db_models import (
    UserModel, AdvanceModel, BranchModel, PaymentModel, ArchiveDayModel, ClientModel, BookingModel,
    SessionModel,
)
from sqlalchemy import func as _sa_func

DATA_DIR = os.getenv("DATA_DIR", os.path.expanduser("~"))
os.makedirs(DATA_DIR, exist_ok=True)

SESSIONS_FILE = os.path.join(DATA_DIR, "carwash_sessions.json")  # больше не читается/пишется — оставлен только как путь для миграции (см. migrate_sessions_to_db.py), GAP-DB1 этап 8
ARCHIVE_FILE  = os.path.join(DATA_DIR, "carwash_archive.json")  # больше не читается/пишется — оставлен только как путь для миграции (см. migrate_archive_to_db.py), GAP-DB1 этап 5
BRANCHES_FILE = os.path.join(DATA_DIR, "carwash_branches.json")  # больше не читается/пишется — оставлен только как путь для миграции (см. migrate_branches_to_db.py), GAP-DB1 этап 3
USERS_FILE    = os.path.join(DATA_DIR, "carwash_users.json")  # больше не читается/пишется — оставлен только как путь для миграции (см. migrate_users_to_db.py), GAP-DB1 этап 1
CLIENTS_FILE  = os.path.join(DATA_DIR, "carwash_clients.json")  # больше не читается/пишется — оставлен только как путь для миграции (см. migrate_clients_to_db.py), GAP-DB1 этап 6
ADVANCES_FILE = os.path.join(DATA_DIR, "carwash_advances.json")  # больше не читается/пишется — оставлен только как путь для миграции (см. migrate_advances_to_db.py), GAP-DB1 этап 2
BOOKINGS_FILE = os.path.join(DATA_DIR, "carwash_bookings.json")  # больше не читается/пишется — оставлен только как путь для миграции (см. migrate_bookings_to_db.py), GAP-DB1 этап 7
PAYMENTS_FILE = os.path.join(DATA_DIR, "carwash_payments.json")  # больше не читается/пишется — оставлен только как путь для миграции (см. migrate_payments_to_db.py), GAP-DB1 этап 4

LOCK_TIMEOUT = 10  # секунд ожидания блокировки, прежде чем сдаться


class Timeout(Exception):
    pass


# Начиная с GAP-DB1 этапа 8 (перенос sessions — последнего домена на JSON)
# эти файловые хелперы (_file_lock/_atomic_write_json/_read_json_locked/
# _write_json_locked/_update_json_locked) больше НИКЕМ не вызываются: все
# 8 доменов теперь на БД, транзакции которой (см. db.get_db_session) дают
# ту же гарантию "всё применилось или ничего", что раньше давала файловая
# блокировка. Оставлены не удалёнными (а не выброшены) — минимальное по
# объёму изменение этого этапа: сам факт, что они стали мёртвым кодом, не
# входит в цель этапа ("перенести sessions", а не "убрать legacy-хелперы"),
# и решение выбросить их — на усмотрение владельца.
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
# GAP-DB1, этап 8 (12.08.2026, ФИНАЛЬНЫЙ ДОМЕН): перенесено на БД
# (SessionModel, см. db_models.py) — тем же принципом, что и остальные 7
# доменов: Postgres в проде через DATABASE_URL, SQLite-фолбэк в деве/
# тестах (см. db.py). Старый SESSIONS_FILE больше НЕ читается/не пишется;
# перенос уже накопленных на проде данных — см. migrate_sessions_to_db.py.
#
# В отличие от остальных доменов, здесь СОХРАНЁН прежний паттерн: в памяти
# процесса держим весь кэш `sessions` (используется по всему проекту не
# только через get_session()/save_sessions(), но и напрямую как
# `sessions.sessions` — см. employee_stats.py/handlers/reports.py), и
# большинство чтений идёт из него; на изменение отдаём наружу ту же самую
# мутируемую ссылку на dict (как и раньше), а на БД пишем ЦЕЛИКОМ через
# save_sessions() при каждом изменении — тот же контракт "мутируй в
# памяти, потом сохрани", что раньше был с _update_json_locked, чтобы не
# переписывать десятки мест по всему проекту, вызывающих get_session()
# затем save_sessions() (см. докстринг SessionModel в db_models.py).

sessions: dict[str, dict] = {}   # branch -> session


def load_sessions():
    """Загружает кэш sessions из БД в память процесса. Вызывается один раз
    при старте процесса (см. run_all.py/bot.py) — ДО старта бота и сайта,
    т.к. оба читают из одного и того же словаря `sessions`."""
    global sessions
    with get_db_session() as db:
        sessions = {row.branch: dict(row.data) for row in db.query(SessionModel).all()}


def save_sessions():
    """Сбрасывает весь текущий кэш sessions в БД (по одной строке на
    филиал, upsert). Используется после прямого изменения sessions[branch]
    в памяти — тот же вызов, что и раньше, поменялся только бэкенд."""
    with get_db_session() as db:
        for branch, session in sessions.items():
            row = db.get(SessionModel, branch)
            if row is None:
                db.add(SessionModel(branch=branch, data=session))
            else:
                row.data = session  # переприсваивание, не мутация — см. комментарий у BranchModel


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
    with get_db_session() as db:
        result: dict = {}
        for row in db.query(ArchiveDayModel).all():
            result.setdefault(row.branch, {})[row.date] = dict(row.day)
        return result


def save_to_archive(branch: str, session: dict):
    date = session.get("date", datetime.now().strftime("%d.%m.%Y"))
    day = {
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
    with get_db_session() as db:
        row = db.get(ArchiveDayModel, (branch, date))
        if row is None:
            db.add(ArchiveDayModel(branch=branch, date=date, day=day))
        else:
            row.day = day


def overwrite_archive_day(branch: str, date: str, day: dict):
    """Полностью заменяет запись конкретного дня в архиве конкретного
    филиала. Используется для ручного исправления испорченных дней
    (например, если день случайно переоткрылся и в него дописались
    машины из другого дня)."""
    with get_db_session() as db:
        row = db.get(ArchiveDayModel, (branch, date))
        if row is None:
            db.add(ArchiveDayModel(branch=branch, date=date, day=day))
        else:
            row.day = day  # переприсваивание, не мутация — см. комментарий у BranchModel


def set_archive_admin_name(branch: str, date: str, name: str) -> bool:
    """Задним числом проставить, кто дежурил администратором в уже
    архивированный день (нужно для истории зарплаты — раньше это поле
    не сохранялось). Возвращает False, если такого дня нет в архиве."""
    with get_db_session() as db:
        row = db.get(ArchiveDayModel, (branch, date))
        if row is None:
            return False
        day = dict(row.day)
        day["admin_name"] = name
        row.day = day
        return True


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
    with get_db_session() as db:
        row = db.get(ArchiveDayModel, (branch, date))
        if row is None:
            if not create_if_missing:
                return False
            day = {
                "date": date, "branch": branch,
                "cars": [], "products": [], "expenses": [], "incomes": [], "loyalty": [],
                "admin_percent": SALARY_ADMIN, "admin_name": admin_name,
            }
            row = ArchiveDayModel(branch=branch, date=date, day=day)
            db.add(row)
        else:
            day = dict(row.day)
        patch_fixed_rates(day, rate_updates, admin_amount)
        row.day = day
        return True


# ── КОНФИГ ФИЛИАЛОВ: админ + сотрудники ─────────────────────────────────────
# GAP-DB1, этап 3 (11.08.2026): перенесено на БД (BranchModel, см.
# db_models.py) — Postgres в проде через DATABASE_URL, SQLite-фолбэк в
# деве/тестах (см. db.py), тем же принципом, что users (этап 1) и advances
# (этап 2). Вложенные структуры (workers/admin_names/boxes/stock/schedules)
# хранятся как JSON-колонки одной строки на филиал — форма данных, которую
# отдают функции ниже (dict с теми же ключами, что был в
# branches_config.json[branch]), не изменилась, вызывающий код не тронут.
# Старый carwash_branches.json больше НЕ читается/не пишется этими
# функциями; перенос уже накопленных на проде данных — см.
# migrate_branches_to_db.py (запускается владельцем вручную один раз при
# деплое этого этапа). Остальные 5 доменов (sessions/archive/bookings/
# clients/payments) пока на JSON — следующие этапы GAP-DB1.

def _branch_row_to_config(row: BranchModel) -> dict:
    return {
        "admin": row.admin,
        "workers": list(row.workers or []),
        "admin_names": list(row.admin_names or []),
        "boxes": [dict(b) for b in (row.boxes or [])],
        "boxes_next_id": row.boxes_next_id,
        "stock": {k: dict(v) for k, v in (row.stock or {}).items()},
        "schedules": {k: dict(v) for k, v in (row.schedules or {}).items()},
    }


def _get_or_create_branch_row(db, branch: str) -> BranchModel:
    row = db.get(BranchModel, branch)
    if row is None:
        row = BranchModel(
            branch=branch, admin=0, workers=[], admin_names=[],
            boxes=[], boxes_next_id=1, stock={}, schedules={},
        )
        db.add(row)
        db.flush()
    return row


def load_branches_config() -> dict:
    """Конфиг всех филиалов — заодно гарантирует, что для каждого филиала
    из config.BRANCHES есть строка в БД (создаёт с дефолтными значениями,
    если ещё нет), как раньше делал JSON-вариант при первой загрузке."""
    with get_db_session() as db:
        for b in BRANCHES:
            _get_or_create_branch_row(db, b)
        return {row.branch: _branch_row_to_config(row) for row in db.query(BranchModel).all()}


def get_branch_config(branch: str) -> dict:
    with get_db_session() as db:
        row = db.get(BranchModel, branch)
        if row is None:
            return {"admin": 0, "workers": [], "admin_names": []}
        return _branch_row_to_config(row)


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
    with get_db_session() as db:
        row = _get_or_create_branch_row(db, branch)
        row.admin = user_id


def get_branch_workers(branch: str) -> list[str]:
    return get_branch_config(branch).get("workers", [])


def add_branch_worker(branch: str, name: str) -> bool:
    """Возвращает False, если сотрудник уже есть."""
    with get_db_session() as db:
        row = _get_or_create_branch_row(db, branch)
        workers = list(row.workers or [])
        if name in workers:
            return False
        workers.append(name)
        row.workers = workers
        return True


def remove_branch_worker(branch: str, name: str) -> bool:
    with get_db_session() as db:
        row = _get_or_create_branch_row(db, branch)
        workers = list(row.workers or [])
        if name not in workers:
            return False
        workers.remove(name)
        row.workers = workers
        return True


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
    with get_db_session() as db:
        row = _get_or_create_branch_row(db, branch)
        names = list(row.admin_names or [])
        if name in names:
            return False
        names.append(name)
        row.admin_names = names
        return True


def remove_branch_admin_name(branch: str, name: str) -> bool:
    with get_db_session() as db:
        row = _get_or_create_branch_row(db, branch)
        names = list(row.admin_names or [])
        if name not in names:
            return False
        names.remove(name)
        row.admin_names = names
        return True


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
    with get_db_session() as db:
        row = _get_or_create_branch_row(db, branch)
        schedules = dict(row.schedules or {})
        schedules[name] = {"work": work_days, "rest": rest_days, "start": start_date}
        row.schedules = schedules


def clear_worker_schedule(branch: str, name: str) -> bool:
    with get_db_session() as db:
        row = _get_or_create_branch_row(db, branch)
        schedules = dict(row.schedules or {})
        if name not in schedules:
            return False
        del schedules[name]
        row.schedules = schedules
        return True


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
# GAP-DB1, этап 1 (11.08.2026): единственный домен, переведённый на БД
# (Postgres в проде через DATABASE_URL, SQLite-фолбэк в деве/тестах —
# см. db.py). Сигнатуры и форма данных (dict {str(user_id): "Имя"}) не
# изменились — вызывающий код (handlers/admin.py, webapp/server.py)
# не тронут. Старый carwash_users.json больше НЕ читается этими функциями;
# перенос уже накопленных на проде данных — см. migrate_users_to_db.py
# (запускается владельцем вручную один раз при деплое этого этапа).
# Остальные 7 доменов (branches/sessions/archive/bookings/clients/
# advances/payments) пока на JSON — следующие этапы GAP-DB1.

def load_users() -> dict:
    with get_db_session() as db:
        return {str(row.user_id): row.name for row in db.query(UserModel).all()}


def save_users(users: dict):
    """Полная перезапись белого списка. На момент этого этапа нигде в
    проекте не вызывается (проверено grep'ом) — сохранена для обратной
    совместимости сигнатуры на случай внешнего кода."""
    with get_db_session() as db:
        db.query(UserModel).delete()
        for uid, name in users.items():
            db.add(UserModel(user_id=int(uid), name=name))


def add_user(user_id: int, name: str):
    with get_db_session() as db:
        existing = db.get(UserModel, user_id)
        if existing:
            existing.name = name
        else:
            db.add(UserModel(user_id=user_id, name=name))


def remove_user(user_id: int) -> bool:
    with get_db_session() as db:
        existing = db.get(UserModel, user_id)
        if not existing:
            return False
        db.delete(existing)
        return True


# ── КЛИЕНТЫ (карточка клиента, история визитов, поиск) ─────────────────────
# GAP-DB1, этап 6 (12.08.2026): перенесено на БД (см. db.py/db_models.py —
# ClientModel), тем же принципом, что и archive в этапе 5: сигнатуры и форма
# возвращаемых данных не изменились. Старый CLIENTS_FILE больше не
# читается/пишется этими функциями — перенос накопленных на проде данных
# см. migrate_clients_to_db.py.
#
# Бывший carwash_clients.json: { normalized_phone: {"phone","name","cars":[...],
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


def _client_row_to_dict(row: ClientModel) -> dict:
    """Сырая карточка клиента в форме прежнего JSON-значения (без
    вычисляемых полей — их добавляет client_summary)."""
    return {
        "phone": row.phone,
        "name": row.name,
        "cars": list(row.cars or []),
        "visits": list(row.visits or []),
        "discount_percent": row.discount_percent,
    }


def _get_or_create_client_row(db, phone: str) -> ClientModel:
    row = db.get(ClientModel, phone)
    if row is None:
        row = ClientModel(phone=phone, name="", cars=[], visits=[], discount_percent=None)
        db.add(row)
    return row


def load_clients() -> dict:
    with get_db_session() as db:
        return {row.phone: _client_row_to_dict(row) for row in db.query(ClientModel).all()}


def find_client(phone: str) -> dict | None:
    phone = normalize_phone(phone)
    if not phone:
        return None
    with get_db_session() as db:
        row = db.get(ClientModel, phone)
        return client_summary(_client_row_to_dict(row)) if row else None


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
    with get_db_session() as db:
        row = db.get(ClientModel, phone)
        if row is None:
            return None
        row.discount_percent = percent
        return client_summary(_client_row_to_dict(row))


def clear_client_discount(phone: str) -> dict | None:
    """Снимает постоянную скидку клиента (NULL в discount_percent — то же
    смысловое отличие "скидка 0%" vs "скидка не задана", что раньше
    выражалось отсутствием ключа в JSON, хотя первое сейчас нигде не
    создаётся)."""
    phone = normalize_phone(phone)
    if not phone:
        return None
    with get_db_session() as db:
        row = db.get(ClientModel, phone)
        if row is None:
            return None
        row.discount_percent = None
        return client_summary(_client_row_to_dict(row))


def apply_client_loyalty_discount(session: dict, phone: str, car_num: int, base_price: int) -> int:
    """GAP-M12: единая модель скидок. Если у клиента с этим телефоном есть
    постоянная скидка (discount_percent, см. set_client_discount), при
    добавлении его машины в кассу автоматически создаётся запись в
    session["loyalty"] — тем же механизмом, что и разовая ручная скидка
    (PUT /api/loyalty). car["price"] при этом НЕ трогается и остаётся
    полной ценой: зарплата мойщика и база % администратора считаются от
    неё (см. calculator.py), а скидка вычитается только из фактически
    принятых денег и видна отдельной строкой «Лояльность» в кассе/отчётах
    — то же поведение, что раньше было только у разовой скидки. Вызывать
    один раз сразу после добавления машины в session["cars"], до
    save_sessions(). Возвращает применённую сумму скидки (0, если у
    клиента нет постоянной скидки)."""
    if not phone:
        return 0
    client = find_client(phone)
    percent = client.get("discount_percent") if client else None
    if not percent:
        return 0
    discount = round(base_price * percent / 100)
    if discount <= 0:
        return 0
    session.setdefault("loyalty", []).append({
        "car_num": car_num, "discount": discount,
        "auto": True, "percent": percent,
    })
    return discount


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

    with get_db_session() as db:
        for phone, label in entries:
            phone = normalize_phone(phone)
            label = (label or "").strip()
            if not phone:
                skipped_invalid += 1
                continue
            row = db.get(ClientModel, phone)
            if row is None:
                db.add(ClientModel(phone=phone, name="", cars=[label] if label else [], visits=[], discount_percent=None))
                added_new += 1
            else:
                cars = list(row.cars or [])
                if label and label not in cars:
                    cars.append(label)
                    row.cars = cars  # переприсваивание, не мутация — см. комментарий у BranchModel
                    updated_existing += 1

    return {"added_new": added_new, "updated_existing": updated_existing, "skipped_invalid": skipped_invalid}


def fix_imported_contact_names(entries: list[tuple[str, str]]) -> dict:
    """Разовое исправление прошлой ошибки: ярлык из контактов (название машины,
    а не имя человека) раньше по ошибке сохранялся прямо в поле name клиента.
    Если текущее имя клиента ТОЧНО совпадает с этим ярлыком (значит, его никто
    вручную не менял после того импорта) — переносит ярлык в cars и очищает
    name, чтобы карточка честно показывала «Без имени» вместо названия машины."""
    fixed = 0
    by_phone = {}
    for phone, label in entries:
        p = normalize_phone(phone)
        if p:
            by_phone[p] = (label or "").strip()

    if by_phone:
        with get_db_session() as db:
            rows = db.query(ClientModel).filter(ClientModel.phone.in_(list(by_phone.keys()))).all()
            for row in rows:
                label = by_phone.get(row.phone)
                if not label:
                    continue
                if row.name == label:
                    row.name = ""
                    cars = list(row.cars or [])
                    if label not in cars:
                        cars.append(label)
                    row.cars = cars
                    fixed += 1

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
    with get_db_session() as db:
        row = db.get(ClientModel, phone)
        if row is None:
            return None
        if name is not None:
            row.name = name.strip()
        if cars is not None:
            row.cars = cars
        return client_summary(_client_row_to_dict(row))


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
    with get_db_session() as db:
        row = _get_or_create_client_row(db, phone)
        if name:
            row.name = name
        cars = list(row.cars or [])
        if car and car not in cars:
            cars.append(car)
            row.cars = cars
        visits = list(row.visits or [])
        visits.append({
            "date": date, "branch": branch, "car": car,
            "total": total, "car_num": car_num,
            "service": service, "time": time, "paid": paid, "status": status,
        })
        row.visits = visits
        return client_summary(_client_row_to_dict(row))


# ── АВАНСЫ СОТРУДНИКОВ ──────────────────────────────────────────────────
# GAP-DB1, этап 2 (11.08.2026): перенесено на БД (см. db.py/db_models.py —
# AdvanceModel), тем же принципом, что и users в этапе 1: сигнатуры и форма
# возвращаемых данных (dict {"idx","date","amount","ts"}) не изменились.
# Старый ADVANCES_FILE больше не читается/пишется этими функциями — перенос
# накопленных на проде данных см. migrate_advances_to_db.py.
# Аванс не привязан к дневной кассе — выдаётся "здесь и сейчас" админом
# филиала и вычитается из недельного/месячного заработка сотрудника
# (см. employee_period_stats в employee_stats.py).

def add_advance(branch: str, name: str, amount: int) -> dict:
    """Записывает выдачу аванса. Возвращает добавленную запись."""
    with get_db_session() as db:
        max_idx = db.query(_sa_func.max(AdvanceModel.idx)).filter(
            AdvanceModel.branch == branch, AdvanceModel.employee_name == name,
        ).scalar()
        idx = (max_idx if max_idx is not None else -1) + 1
        row = AdvanceModel(
            branch=branch, employee_name=name, idx=idx,
            date=datetime.now().strftime("%d.%m.%Y"),
            amount=amount, ts=time.time(),
        )
        db.add(row)
        db.flush()
        return {"idx": row.idx, "date": row.date, "amount": row.amount, "ts": row.ts}


def get_employee_advances(branch: str, name: str,
                           date_from: datetime | None = None,
                           date_to: datetime | None = None) -> list[dict]:
    """Список авансов сотрудника, опционально отфильтрованный по датам
    (date_from/date_to — datetime, включительно). Без фильтра — все авансы."""
    with get_db_session() as db:
        rows = db.query(AdvanceModel).filter(
            AdvanceModel.branch == branch, AdvanceModel.employee_name == name,
        ).order_by(AdvanceModel.idx).all()
        entries = [{"idx": r.idx, "date": r.date, "amount": r.amount, "ts": r.ts} for r in rows]
    if date_from is None and date_to is None:
        return entries
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
    with get_db_session() as db:
        row = db.query(AdvanceModel).filter(
            AdvanceModel.branch == branch, AdvanceModel.employee_name == name, AdvanceModel.idx == idx,
        ).first()
        if not row:
            return False
        db.delete(row)
        return True


# ── ЗАПИСИ (ЖУРНАЛ ЗАПИСИ / BOOKINGS) ───────────────────────────────────────
# GAP-DB1, этап 7 (12.08.2026): перенесено на БД (см. db.py/db_models.py —
# BookingModel), тем же принципом, что и clients в этапе 6: сигнатуры и
# форма возвращаемых данных не изменились. Старый BOOKINGS_FILE больше не
# читается/пишется этими функциями — перенос накопленных на проде данных
# см. migrate_bookings_to_db.py.
#
# Бывший carwash_bookings.json: { branch: { "ДД.ММ.ГГГГ": [ {запись}, ... ] } }
# Запись — это будущий/сегодняшний слот в боксе (в отличие от "машины" в
# sessions/cars, которая появляется в кассе по факту приезда клиента).
# id записи уникален глобально (как и car.num — но car.num уникален только
# в рамках одной смены филиала, а запись должна однозначно адресоваться без
# указания филиала/даты — отсюда сквозной id, теперь первичный ключ
# BookingModel вместо ключа верхнего уровня словаря).
#
# Статусы записи (BOOKING_STATUSES зеркалируется в webapp/server.py):
#   waiting     — ожидание (по умолчанию, только создана)
#   confirmed   — клиент подтвердил, что приедет
#   arrived     — клиент приехал, машина в боксе
#   no_show     — клиент не пришёл (бокс/время считаются снова свободными)
#   in_progress — мойка в процессе
#   done        — оплачено/завершено
#
# Бокс (box) — независимая сущность филиала (GAP-BOX1): физический пост
# мойки со своим id и названием, задаётся и меняется отдельно от списка
# сотрудников (carwash_branches.json → branch.boxes). Раньше бокс #N был
# жёстко равен N-му сотруднику филиала по списку — это ломалось, если
# количество постов не совпадало с количеством сотрудников, или если
# порядок сотрудников менялся. Теперь запись (booking) хранит box (id
# бокса) и employee (имя сотрудника) как два независимых поля — какой
# сотрудник назначен на запись, выбирается отдельно и не выводится
# автоматически из номера бокса.
#
# find_conflicting_booking/get_public_available_slots/_pick_free_box_for_slot/
# find_bookings_by_phone/set_booking_status НЕ меняются в этом этапе — они
# уже были написаны через get_bookings()/load_bookings() и продолжают
# работать как есть поверх их новой БД-реализации ниже.

def _booking_row_to_dict(row: BookingModel) -> dict:
    return {
        "id": row.id,
        "branch": row.branch,
        "date": row.date,
        "box": row.box,
        "start_time": row.start_time,
        "end_time": row.end_time,
        "employee": row.employee,
        "body_type": row.body_type,
        "car": row.car,
        "service_keys": list(row.service_keys or []),
        "custom_services": list(row.custom_services or []),
        "product_keys": list(row.product_keys or []),
        "price": row.price,
        "price_calc": row.price_calc,
        "price_override": row.price_override,
        "payment": row.payment,
        "payment_split": dict(row.payment_split) if row.payment_split else row.payment_split,
        "comment": row.comment,
        "phone": row.phone,
        "client_name": row.client_name,
        "status": row.status,
        "car_num": row.car_num,
        "prepayment": dict(row.prepayment) if row.prepayment else row.prepayment,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def load_bookings() -> dict:
    """Все записи в прежней форме { branch: { date: [запись, ...] } },
    списки в порядке создания (по возрастанию id)."""
    with get_db_session() as db:
        rows = db.query(BookingModel).order_by(BookingModel.id).all()
        out: dict = {}
        for row in rows:
            out.setdefault(row.branch, {}).setdefault(row.date, []).append(_booking_row_to_dict(row))
        return out


def get_bookings(branch: str, date: str) -> list[dict]:
    """Все записи филиала на конкретную дату (ДД.ММ.ГГГГ), в порядке создания."""
    with get_db_session() as db:
        rows = (
            db.query(BookingModel)
            .filter(BookingModel.branch == branch, BookingModel.date == date)
            .order_by(BookingModel.id)
            .all()
        )
        return [_booking_row_to_dict(r) for r in rows]


def get_branch_boxes(branch: str) -> list[dict]:
    """Боксы филиала как независимая сущность (GAP-BOX1): список
    {"box": id, "name": name}, отсортированный по id. Не привязан к
    списку сотрудников — количество и состав боксов настраиваются
    отдельно через add_branch_box/rename_branch_box/remove_branch_box."""
    boxes = get_branch_config(branch).get("boxes", [])
    return [
        {"box": b["id"], "name": b.get("name") or f"Бокс {b['id']}"}
        for b in sorted(boxes, key=lambda b: b["id"])
    ]


def add_branch_box(branch: str, name: str) -> dict:
    """Добавляет новый бокс филиалу. id — сквозной по филиалу (монотонный
    счётчик `boxes_next_id`, а не max(текущих id)+1), чтобы старые id не
    переиспользовались после удаления бокса, даже если это был бокс с
    наибольшим id (записи с уже удалённым box.id должны оставаться
    однозначно отличимы от нового бокса с тем же порядковым местом)."""
    with get_db_session() as db:
        row = _get_or_create_branch_row(db, branch)
        boxes = [dict(b) for b in (row.boxes or [])]
        next_id = row.boxes_next_id
        if next_id is None:
            next_id = max([b["id"] for b in boxes], default=0) + 1
        box = {"id": next_id, "name": name.strip() or f"Бокс {next_id}"}
        boxes.append(box)
        row.boxes = boxes
        row.boxes_next_id = next_id + 1
        return box


def rename_branch_box(branch: str, box_id: int, name: str) -> bool:
    """Возвращает False, если бокса с таким id нет у филиала."""
    with get_db_session() as db:
        row = _get_or_create_branch_row(db, branch)
        boxes = [dict(b) for b in (row.boxes or [])]
        renamed = False
        for b in boxes:
            if b["id"] == box_id:
                b["name"] = name.strip() or f"Бокс {box_id}"
                renamed = True
                break
        if renamed:
            row.boxes = boxes
        return renamed


def remove_branch_box(branch: str, box_id: int) -> bool:
    """Возвращает False, если бокса с таким id нет у филиала. Не трогает
    записи (bookings), которые уже ссылаются на этот box.id — вызывающая
    сторона (webapp/server.py) сама решает, разрешать ли удаление бокса
    с активными записями (см. api_remove_box)."""
    with get_db_session() as db:
        row = _get_or_create_branch_row(db, branch)
        boxes = [dict(b) for b in (row.boxes or [])]
        new_boxes = [b for b in boxes if b["id"] != box_id]
        if len(new_boxes) == len(boxes):
            return False
        row.boxes = new_boxes
        return True


# ── СКЛАД: ОСТАТКИ ТОВАРОВ (GAP-P1) ─────────────────────────────────────────
# branches_config.json[branch]["stock"]: {product_key: {"qty": int, "min_qty": int}}.
# Отсутствие ключа товара в этом словаре = остаток НЕ отслеживается (товар
# ведёт себя как до GAP-P1, без ограничений) — так задумано, чтобы включение
# модуля не сломало продажи сразу после деплоя, пока никто не ввёл реальные
# цифры остатков. Как только для товара явно задан остаток (set_branch_stock),
# он становится отслеживаемым и списывается при каждой продаже.

def get_branch_stock(branch: str) -> dict:
    """Остатки товаров филиала: {key: {"qty": int, "min_qty": int}}. Товары
    без записи здесь считаются неотслеживаемыми (см. комментарий выше)."""
    return dict(get_branch_config(branch).get("stock", {}))


def set_branch_stock(branch: str, key: str, qty: int | None = None, min_qty: int | None = None) -> dict:
    """Калибровка/пополнение: задаёт АБСОЛЮТНЫЙ остаток товара (не дельту) и/
    или минимальный порог для уведомления о низком остатке. Создаёт запись
    отслеживания для товара, если её ещё не было (с этого момента товар
    начинает списываться при продаже). Хотя бы один из qty/min_qty должен
    быть передан, иначе запись не меняется."""
    with get_db_session() as db:
        row = _get_or_create_branch_row(db, branch)
        stock = {k: dict(v) for k, v in (row.stock or {}).items()}
        entry = dict(stock.get(key, {"qty": 0, "min_qty": 0}))
        if qty is not None:
            entry["qty"] = max(0, int(qty))
        if min_qty is not None:
            entry["min_qty"] = max(0, int(min_qty))
        stock[key] = entry
        row.stock = stock
        return dict(entry)


def clear_branch_stock(branch: str, key: str) -> bool:
    """Убирает товар из отслеживания склада — остаток снова становится
    неограниченным, как до GAP-P1. Возвращает False, если товар и так не
    отслеживался."""
    with get_db_session() as db:
        row = _get_or_create_branch_row(db, branch)
        stock = {k: dict(v) for k, v in (row.stock or {}).items()}
        if key not in stock:
            return False
        del stock[key]
        row.stock = stock
        return True


def try_decrement_branch_stock(branch: str, key: str, amount: int = 1) -> tuple[bool, int | None, bool]:
    """Пытается списать товар со склада филиала при продаже. Возвращает
    (ok, new_qty, crossed_threshold):
    - товар не отслеживается → всегда (True, None, False) — без ограничений;
    - отслеживается, но остатка не хватает → (False, qty, False), ничего не
      меняется — вызывающая сторона решает, блокировать продажу или нет;
    - отслеживается и остатка хватает → списывает и возвращает (True,
      new_qty, crossed), где crossed=True только в момент, когда остаток
      ВПЕРВЫЕ опустился до порога min_qty или ниже (чтобы не слать
      уведомление на каждую последующую продажу того же товара)."""
    with get_db_session() as db:
        row = _get_or_create_branch_row(db, branch)
        stock = {k: dict(v) for k, v in (row.stock or {}).items()}
        entry = stock.get(key)
        if entry is None:
            return True, None, False
        qty = int(entry.get("qty", 0))
        min_qty = int(entry.get("min_qty", 0))
        if qty < amount:
            return False, qty, False
        new_qty = qty - amount
        entry = dict(entry)
        entry["qty"] = new_qty
        stock[key] = entry
        row.stock = stock
        crossed = new_qty <= min_qty < qty
        return True, new_qty, crossed


def increment_branch_stock(branch: str, key: str, amount: int = 1) -> int | None:
    """Возвращает товар на склад — отмена списания (например, при удалении
    продажи товара из кассы). Если товар не отслеживается — ничего не
    делает и возвращает None."""
    with get_db_session() as db:
        row = _get_or_create_branch_row(db, branch)
        stock = {k: dict(v) for k, v in (row.stock or {}).items()}
        entry = stock.get(key)
        if entry is None:
            return None
        entry = dict(entry)
        entry["qty"] = int(entry.get("qty", 0)) + amount
        stock[key] = entry
        row.stock = stock
        return entry["qty"]


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


def get_booking(booking_id: int) -> dict | None:
    with get_db_session() as db:
        row = db.get(BookingModel, booking_id)
        return _booking_row_to_dict(row) if row else None


def create_booking(branch: str, date: str, box: int, start_time: str, end_time: str,
                    employee: str = "", body_type: str = "", car: str = "",
                    service_keys: list[str] | None = None, custom_services: list[dict] | None = None,
                    product_keys: list[str] | None = None, price: int = 0, price_calc: int = 0,
                    price_override: int | None = None, payment: str = "",
                    payment_split: dict | None = None, comment: str = "",
                    phone: str = "", client_name: str = "", status: str = "waiting") -> dict:
    """Создаёт запись и возвращает её. id выдаётся сквозным счётчиком
    (максимум существующих id + 1) в той же транзакции, что и вставка
    строки — та же гарантия против совпадения id при параллельных
    созданиях, что раньше давала файловая блокировка."""
    now = datetime.now().isoformat(timespec="seconds")
    with get_db_session() as db:
        max_id = db.query(_sa_func.max(BookingModel.id)).scalar()
        row = BookingModel(
            id=(max_id or 0) + 1,
            branch=branch,
            date=date,
            box=box,
            start_time=start_time,
            end_time=end_time,
            employee=employee,
            body_type=body_type,
            car=car,
            service_keys=service_keys or [],
            custom_services=custom_services or [],
            product_keys=product_keys or [],
            price=price,
            price_calc=price_calc,
            price_override=price_override,
            payment=payment,
            payment_split=payment_split,
            comment=comment,
            phone=normalize_phone(phone) if phone else "",
            client_name=client_name,
            status=status,
            car_num=None,   # номер машины в кассе смены, если запись уже конвертирована (статус arrived)
            prepayment=None,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        return _booking_row_to_dict(row)


def update_booking(booking_id: int, **fields) -> dict | None:
    """Точечное обновление записи по id. branch/date — обычные колонки,
    поэтому перенос записи на другую дату/филиал/бокс — такое же присвоение
    полю, как и любое другое (раньше в JSON-версии для этого требовался
    отдельный перенос записи между вложенными списками файла). Ключи в
    fields со значением None игнорируются (тот же контракт, что был у
    JSON-версии) — вызывающий код должен передавать только реально
    изменяемые поля."""
    with get_db_session() as db:
        row = db.get(BookingModel, booking_id)
        if row is None:
            return None
        for k, v in fields.items():
            if v is not None:
                setattr(row, k, v)
        row.updated_at = datetime.now().isoformat(timespec="seconds")
        db.flush()
        return _booking_row_to_dict(row)


def set_booking_status(booking_id: int, status: str) -> dict | None:
    return update_booking(booking_id, status=status)


# ── ПУБЛИЧНАЯ ВИТРИНА ЗАПИСИ (GAP-CLIENT-PORTAL, этап 1) ────────────────────
# Упрощённая витрина для клиента: список свободных времён (не сетка боксов),
# без верификации номера телефона (решение владельца 11.08.2026) — телефон,
# который ввёл клиент, принимается как есть и используется как единственный
# идентификатор для последующего просмотра/переноса/отмены его записи.
PUBLIC_WORK_START_MIN = 8 * 60   # 08:00, совпадает с сеткой сайта (booking.html)
PUBLIC_WORK_END_MIN = 20 * 60    # 20:00
PUBLIC_SLOT_STEP_MIN = 30        # шаг, с которым перебираются кандидаты на старт
PUBLIC_DEFAULT_DURATION_MIN = 60  # нет поля "длительность" у услуг (config.SERVICES
                                   # хранит только цену/%) — единая длительность слота
                                   # для витрины, пока не появится другой источник


def get_public_available_slots(branch: str, date: str, duration_min: int = PUBLIC_DEFAULT_DURATION_MIN) -> list[str]:
    """Список свободных времён начала (["HH:MM", ...]) на дату для публичной
    витрины: слот свободен, если хотя бы один бокс филиала свободен на весь
    интервал [start, start+duration). Не привязан к конкретному боксу —
    бокс подбирается автоматически при создании записи (см. create_booking
    вызывающей стороной). Если у филиала нет ни одного бокса — возвращает []."""
    boxes = get_branch_boxes(branch)
    if not boxes:
        return []
    existing = [b for b in get_bookings(branch, date) if b.get("status") != "no_show"]
    today = datetime.now().strftime("%d.%m.%Y")
    now_min = datetime.now().hour * 60 + datetime.now().minute if date == today else -1
    slots = []
    start = PUBLIC_WORK_START_MIN
    while start + duration_min <= PUBLIC_WORK_END_MIN:
        if start >= now_min:
            end = start + duration_min
            free_box = None
            for box in boxes:
                conflict = False
                for b in existing:
                    if b.get("box") != box["box"]:
                        continue
                    ex_start, ex_end = _time_to_minutes(b.get("start_time", "")), _time_to_minutes(b.get("end_time", ""))
                    if start < ex_end and ex_start < end:
                        conflict = True
                        break
                if not conflict:
                    free_box = box["box"]
                    break
            if free_box is not None:
                slots.append(f"{start // 60:02d}:{start % 60:02d}")
        start += PUBLIC_SLOT_STEP_MIN
    return slots


def _pick_free_box_for_slot(branch: str, date: str, start_time: str, end_time: str) -> int | None:
    """Выбирает первый свободный бокс филиала для интервала — используется
    публичной витриной, у которой (в отличие от персонала) нет UI выбора
    бокса. Возвращает None, если слот уже занят во всех боксах (гонка между
    получением списка слотов и отправкой формы)."""
    for box in get_branch_boxes(branch):
        if find_conflicting_booking(branch, date, box["box"], start_time, end_time) is None:
            return box["box"]
    return None


def find_bookings_by_phone(phone: str, include_past: bool = False) -> list[dict]:
    """Все записи клиента по номеру телефона (нормализуется так же, как при
    создании), по всем филиалам/датам. Без верификации — единственная
    проверка принадлежности записи клиенту на публичной витрине это
    совпадение нормализованного номера. Отсортировано по дате/времени,
    новые сверху."""
    norm = normalize_phone(phone)
    if not norm:
        return []
    today = datetime.now().strftime("%d.%m.%Y")
    today_min = datetime.now().hour * 60 + datetime.now().minute
    out = []
    for branch, days in load_bookings().items():
        for date, items in days.items():
            for b in items:
                if b.get("phone") != norm:
                    continue
                if not include_past and b.get("status") in ("done", "no_show"):
                    continue
                out.append(b)
    def _sort_key(b):
        d = b.get("date", "")
        try:
            dd, mm, yy = d.split(".")
            dkey = (int(yy), int(mm), int(dd))
        except ValueError:
            dkey = (0, 0, 0)
        return (dkey, _time_to_minutes(b.get("start_time", "")))
    out.sort(key=_sort_key, reverse=True)
    return out


def delete_booking(booking_id: int) -> bool:
    with get_db_session() as db:
        row = db.get(BookingModel, booking_id)
        if row is None:
            return False
        db.delete(row)
        return True


# ── ОНЛАЙН-ОПЛАТА (GAP-PAY1) ────────────────────────────────────────────────
# carwash_payments.json: { payment_id: {запись платежа} }. payment_id выдаёт
# провайдер (payment_provider.py) — в мок-режиме это "mock_<hex>", в боевом
# режиме — id платежа ЮKassa; в обоих случаях он уникален глобально, поэтому
# отдельный файл не разбит по филиалам (филиал хранится внутри записи).
#
# Два назначения (purpose):
#   "advance" — предоплата (аванс) клиента по ЗАПИСИ (booking_id): деньги ещё
#     не попадают в кассу смены, только помечают booking.prepayment. Когда
#     запись конвертируется в машину (см. webapp/server.py:
#     _maybe_convert_booking_to_car), предоплаченная часть автоматически
#     уходит в payment_split машины методом "онлайн", остаток — обычным
#     способом оплаты записи.
#   "car" — доплата/оплата по уже существующей машине В КАССЕ (car_num):
#     применяется сразу к payment_split машины текущей смены.
#
# "Онлайн" переиспользует существующий бакет "безнал" в calculator.py
# (см. комментарий там) — деньги ЮKassa поступают на расчётный счёт, как и
# любая другая безналичная оплата, отдельного бакета в отчётах не заводим.

def _payment_row_to_record(row: PaymentModel) -> dict:
    return {
        "id": row.id,
        "branch": row.branch,
        "purpose": row.purpose,
        "booking_id": row.booking_id,
        "car_num": row.car_num,
        "amount": row.amount,
        "description": row.description,
        "phone": row.phone,
        "client_name": row.client_name,
        "status": row.status,
        "provider": row.provider,
        "confirmation_url": row.confirmation_url,
        "applied": row.applied,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "paid_at": row.paid_at,
    }


def load_payments() -> dict:
    with get_db_session() as db:
        return {row.id: _payment_row_to_record(row) for row in db.query(PaymentModel).all()}


def get_payment(payment_id: str) -> dict | None:
    with get_db_session() as db:
        row = db.get(PaymentModel, payment_id)
        return _payment_row_to_record(row) if row else None


def create_payment(branch: str, purpose: str, amount: int, description: str = "",
                    booking_id: int | None = None, car_num: int | None = None,
                    phone: str = "", client_name: str = "") -> dict:
    """Создаёт платёжную сессию у провайдера (боевого или мок — см.
    payment_provider.get_provider()) и сохраняет запись о ней. Бросает
    payment_provider.PaymentProviderError, если провайдер недоступен —
    вызывающая сторона (webapp/server.py) превращает это в HTTP 502."""
    provider = get_provider()
    resp = provider.create_payment(
        amount=amount, description=description or "Оплата CarWash",
        metadata={"branch": branch, "purpose": purpose, "booking_id": booking_id, "car_num": car_num},
    )
    now = datetime.now().isoformat(timespec="seconds")
    record = {
        "id": resp["id"],
        "branch": branch,
        "purpose": purpose,
        "booking_id": booking_id,
        "car_num": car_num,
        "amount": int(amount),
        "description": description,
        "phone": normalize_phone(phone) if phone else "",
        "client_name": client_name,
        "status": resp.get("status", "pending"),
        "provider": provider.name,
        "confirmation_url": resp.get("confirmation_url", ""),
        "applied": False,
        "created_at": now,
        "updated_at": now,
        "paid_at": None,
    }

    with get_db_session() as db:
        db.add(PaymentModel(**record))
    return record


def _apply_car_payment(record: dict) -> None:
    """purpose == 'car': добавляет оплаченную сумму в payment_split машины
    кассы текущей смены методом 'онлайн'. Ничего не делает (тихо), если
    машина не найдена (могла быть удалена) — платёж всё равно помечается
    применённым, повторно искать машину незачем."""
    session = get_session(record["branch"])
    car = next((c for c in session.get("cars", []) if c["num"] == record["car_num"]), None)
    if not car:
        return
    if car.get("payment_split"):
        return  # уже есть раздельная оплата — не перезаписываем молча
    paid = min(record["amount"], car["price"])
    if paid <= 0:
        return
    split = {"онлайн": paid}
    remainder = car["price"] - paid
    if remainder > 0:
        split[car.get("payment") or "нал"] = remainder
    car["payment_split"] = split
    save_sessions()


def apply_payment_success(payment_id: str) -> dict | None:
    """Идемпотентно применяет успешную оплату: помечает запись платежа
    succeeded и один раз проводит побочный эффект (booking.prepayment для
    purpose='advance', payment_split машины для purpose='car'). Повторный
    вызов для уже применённого платежа ничего не меняет и просто отдаёт
    текущую запись. Возвращает None, если платёж с таким id не найден."""
    with get_db_session() as db:
        row = db.get(PaymentModel, payment_id)
        if not row:
            return None
        if not row.applied:
            now = datetime.now().isoformat(timespec="seconds")
            row.status = "succeeded"
            row.paid_at = now
            row.updated_at = now
            row.applied = True
            db.flush()
        record = _payment_row_to_record(row)
    # Побочный эффект — уже вне транзакции платежа, чтобы не держать её на
    # время обращения к carwash_bookings.json/sessions (тот же принцип, что
    # раньше давало снятие файловой блокировки до вызова update_booking).
    if record["purpose"] == "advance" and record.get("booking_id"):
        update_booking(record["booking_id"], prepayment={
            "amount": record["amount"], "status": "paid",
            "payment_id": record["id"], "paid_at": record["paid_at"],
        })
    elif record["purpose"] == "car" and record.get("car_num"):
        _apply_car_payment(record)
    return record


def mark_payment_canceled(payment_id: str) -> dict | None:
    with get_db_session() as db:
        row = db.get(PaymentModel, payment_id)
        if not row or row.applied:
            return _payment_row_to_record(row) if row else None
        row.status = "canceled"
        row.updated_at = datetime.now().isoformat(timespec="seconds")
        db.flush()
        return _payment_row_to_record(row)


# ── ПРИВЯЗКА ПОЛЬЗОВАТЕЛЯ К ФИЛИАЛУ (на сегодняшнюю смену) ─────────────────
# Храним в user_data контекста telegram (per-chat), не здесь — см. handlers.
