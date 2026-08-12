"""
Backend Mini App для CarWash-бота.
Переиспользует существующие sessions.py / calculator.py / config.py —
никакой отдельной базы данных, те же файлы, что использует сам бот.

Запуск (для теста локально):
    pip install fastapi uvicorn --break-system-packages
    uvicorn webapp.server:app --reload --port 8000

Для Telegram Mini App нужен публичный HTTPS-адрес (ngrok / Render / Railway),
см. README в этой папке.
"""
import sys, os, hashlib, hmac, json, tempfile, asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from urllib.parse import parse_qsl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import (
    TOKEN, OWNER_ID, BRANCHES, BODY_TYPES, BODY_TYPE_ORDER, SERVICES, PRODUCTS,
    PAYMENT_TYPES, get_service_price,
)
from sessions import (
    get_session, save_sessions, save_to_archive, reset_session,
    get_branch_workers, get_branch_admin, is_branch_admin, set_branch_admin,
    add_branch_worker, remove_branch_worker,
    get_branch_admin_names, add_branch_admin_name, remove_branch_admin_name,
    get_session_admin_name, set_session_admin_name, set_archive_admin_name,
    load_archive, load_users, save_users, add_user, remove_user,
    set_worker_schedule, clear_worker_schedule, get_worker_schedule,
    get_schedule_status, is_working_on,
    normalize_phone, find_client, search_clients, upsert_client_visit, load_clients, client_summary, update_client,
    set_client_discount, clear_client_discount,
    get_branch_boxes, get_bookings, get_booking, create_booking, update_booking,
    set_booking_status, delete_booking, find_conflicting_booking,
)
from calculator import calculate_summary
from pdf_generator import generate_pdf
from xlsx_generator import generate_xlsx
from history_log import log_action, get_history
from presets import list_presets, add_preset, delete_preset
from notify import notify_user
from webapp.auth_web import (
    LoginIn, login as site_login, logout as site_logout, get_session as get_site_session,
)

MONTHS_RU = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}

app = FastAPI(title="CarWash Mini App API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Проверка подлинности данных Telegram WebApp ────────────────────────────
def verify_init_data(init_data: str) -> dict:
    """Проверяет подпись initData, которую Telegram передаёт при открытии Mini App.
    См. https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app"""
    if not init_data:
        raise HTTPException(401, "Нет данных авторизации")
    parsed = dict(parse_qsl(init_data))
    recv_hash = parsed.pop("hash", "")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if calc_hash != recv_hash:
        raise HTTPException(401, "Неверная подпись initData")
    return json.loads(parsed.get("user", "{}"))


def auth(x_init_data: str = Header(default=""), x_site_token: str = Header(default="")) -> dict:
    # В деве можно временно закомментировать verify и просто распарсить user.
    return verify_init_data(x_init_data)


def auth_optional(x_init_data: str = Header(default=""), x_site_token: str = Header(default="")) -> dict:
    try:
        return verify_init_data(x_init_data)
    except HTTPException:
        return {}


def current_user_id(x_init_data: str = Header(default=""), x_site_token: str = Header(default="")) -> int:
    user = auth_optional(x_init_data)
    return int(user.get("id", 0))


def current_user_name(x_init_data: str = Header(default=""), x_site_token: str = Header(default="")) -> str:
    user = auth_optional(x_init_data)
    return user.get("first_name", "") or user.get("username", "") or "—"


def find_user_id_by_name(name: str) -> int:
    """Ищем telegram id пользователя по имени среди тех, кому уже выдан доступ
    (используется, чтобы уведомить сотрудника при добавлении, если он уже
    есть в списке пользователей)."""
    for uid, uname in load_users().items():
        if uname.strip().lower() == name.strip().lower():
            return int(uid)
    return 0


def is_whitelisted(uid: int) -> bool:
    """Владелец или пользователь из белого списка (carwash_users.json).
    Как только владелец удаляет человека из белого списка (/removeuser или
    Mini App → Пользователи), эта функция сразу перестаёт его пускать —
    доступ к Mini App отзывается немедленно, а не только к чат-боту."""
    if uid == OWNER_ID:
        return True
    users = load_users()
    return str(uid) in users


def require_access(x_init_data: str = Header(default=""), x_site_token: str = Header(default="")) -> int:
    """Базовая проверка для ЛЮБОГО запроса, читающего/меняющего данные кассы:
    пользователь должен быть в белом списке (Telegram) ИЛИ иметь валидный
    сайтовый токен (вход по общему паролю на сайте). Раньше многие ручки
    вообще не проверяли, кто стучится — здесь мы это закрываем."""
    site = get_site_session(x_site_token)
    if site:
        return 0  # у сайтовых пользователей нет telegram id — 0 означает "веб-пользователь"
    uid = current_user_id(x_init_data)
    if not is_whitelisted(uid):
        raise HTTPException(403, "Доступ отозван или не выдан. Обратитесь к владельцу.")
    return uid


def require_branch_admin(branch: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    site = get_site_session(x_site_token)
    if site:
        if site["role"] not in ("админ", "владелец"):
            raise HTTPException(403, "Нет прав администратора филиала")
        if site["role"] == "админ" and site.get("branch") != branch:
            raise HTTPException(403, "Нет прав администратора этого филиала")
        return 0
    uid = current_user_id(x_init_data)
    if not is_whitelisted(uid):
        raise HTTPException(403, "Доступ отозван или не выдан. Обратитесь к владельцу.")
    if not is_branch_admin(uid, branch):
        raise HTTPException(403, "Нет прав администратора филиала")
    return uid


def require_owner(x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    site = get_site_session(x_site_token)
    if site:
        if site["role"] != "владелец":
            raise HTTPException(403, "Только для владельца")
        return 0
    uid = current_user_id(x_init_data)
    if uid != OWNER_ID:
        raise HTTPException(403, "Только для владельца")
    return uid


# ── Веб-вход по общему паролю (имя + должность) ─────────────────────────────
@app.post("/api/site/login")
def api_site_login(body: LoginIn):
    return site_login(body)


@app.post("/api/site/logout")
def api_site_logout(x_site_token: str = Header(default="")):
    site_logout(x_site_token)
    return {"ok": True}


@app.get("/api/site/me")
def api_site_me(x_site_token: str = Header(default="")):
    site = get_site_session(x_site_token)
    if not site:
        raise HTTPException(401, "Не авторизован")
    return site


# ── Модели запросов ─────────────────────────────────────────────────────────
class CarIn(BaseModel):
    branch: str
    employee: str
    body_type: str
    service_keys: list[str] = []
    custom_services: list[dict] = []       # [{"name","price","percent"}]
    car: str = ""
    payment: str
    payment_split: Optional[Dict[str, int]] = None   # {"нал": 800, "безнал": 1200}
    price_override: Optional[int] = None   # ручная правка итоговой суммы (скидка/наценка)
    comment: str = ""
    phone: str = ""          # телефон клиента (необязательно) — карточка клиента
    client_name: str = ""    # имя клиента (необязательно)


class LoyaltyIn(BaseModel):
    branch: str
    car_num: int
    discount: int


class ExpenseIn(BaseModel):
    branch: str
    name: str
    amount: int


class IncomeIn(BaseModel):
    branch: str
    name: str
    amount: int
    payment: str = "нал"
    payment_split: Optional[Dict[str, int]] = None


class ProductIn(BaseModel):
    branch: str
    key: str
    payment: str = "нал"


class WorkerIn(BaseModel):
    branch: str
    name: str
    x_init_data: str = ""


class ScheduleIn(BaseModel):
    branch: str
    name: str
    work_days: int
    rest_days: int
    start_date: str  # YYYY-MM-DD


class BranchAdminIn(BaseModel):
    branch: str
    user_id: int


class AdminNameIn(BaseModel):
    branch: str
    name: str


class AdminOnDutyIn(BaseModel):
    branch: str
    name: str  # "" — снять дежурного


class AdminHistoryBackfillIn(BaseModel):
    branch: str
    assignments: Dict[str, str]  # {"10.07.2026": "Салим", "09.07.2026": "Иззет", ...}


class CarEditIn(BaseModel):
    employee: Optional[str] = None
    body_type: Optional[str] = None
    service_keys: Optional[list[str]] = None
    custom_services: Optional[list[dict]] = None
    car: Optional[str] = None
    payment: Optional[str] = None
    payment_split: Optional[Dict[str, int]] = None
    price_override: Optional[int] = None   # ручная правка итоговой суммы; чтобы снять — передать 0 не получится, см. clear_price_override
    clear_price_override: bool = False     # true → вернуть цену к расчётной по услугам
    comment: Optional[str] = None
    status: Optional[str] = None
    phone: Optional[str] = None
    client_name: Optional[str] = None


class ClientUpdateIn(BaseModel):
    name: Optional[str] = None
    cars: Optional[list[str]] = None


class ClientDiscountIn(BaseModel):
    percent: float


class CarStatusIn(BaseModel):
    status: str  # "in_progress" | "done"


class PresetIn(BaseModel):
    branch: str
    name: str
    service_keys: list[str] = []
    custom_services: list[dict] = []


class UserIn(BaseModel):
    user_id: int
    name: str = "Без имени"


# ── Запись (журнал записи / bookings) ───────────────────────────────────────
# Статусы записи. "waiting"/"confirmed"/"arrived"/"no_show" — этапы до начала
# мойки (из макета модалки), "in_progress"/"done" — во время и после (как в
# сетке боксов макета). См. sessions.py, раздел "ЗАПИСИ".
BOOKING_STATUSES = {"waiting", "confirmed", "arrived", "no_show", "in_progress", "done"}


class BookingIn(BaseModel):
    branch: str
    date: str          # ДД.ММ.ГГГГ
    box: int
    start_time: str    # ЧЧ:ММ
    end_time: str      # ЧЧ:ММ
    employee: str = ""
    body_type: str = ""
    car: str = ""
    service_keys: list[str] = []
    custom_services: list[dict] = []   # [{"name","price","percent"}]
    product_keys: list[str] = []
    price_override: Optional[int] = None
    payment: str = ""
    payment_split: Optional[Dict[str, int]] = None
    comment: str = ""
    phone: str = ""
    client_name: str = ""
    status: str = "waiting"


class BookingEditIn(BaseModel):
    branch: Optional[str] = None
    date: Optional[str] = None
    box: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    employee: Optional[str] = None
    body_type: Optional[str] = None
    car: Optional[str] = None
    service_keys: Optional[list[str]] = None
    custom_services: Optional[list[dict]] = None
    product_keys: Optional[list[str]] = None
    price_override: Optional[int] = None
    clear_price_override: bool = False
    payment: Optional[str] = None
    payment_split: Optional[Dict[str, int]] = None
    comment: Optional[str] = None
    phone: Optional[str] = None
    client_name: Optional[str] = None
    status: Optional[str] = None


class BookingStatusIn(BaseModel):
    status: str


# ── Справочники (без авторизации — статичные данные) ───────────────────────
@app.get("/api/config")
def api_config():
    return {
        "branches": BRANCHES,
        "body_types": [{"key": k, "name": BODY_TYPES[k]} for k in BODY_TYPE_ORDER],
        "services": [
            {"key": k, "name": v["name"], "percent": v["percent"],
             "prices": v["prices"] if isinstance(v["prices"], dict) else
                       {bt: v["prices"] for bt in BODY_TYPE_ORDER}}
            for k, v in SERVICES.items()
        ],
        "products": [{"key": k, "name": v["name"], "price": v["price"]} for k, v in PRODUCTS.items()],
        "payment_types": PAYMENT_TYPES,
    }


@app.get("/api/workers")
def api_workers(branch: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    return {
        "workers": get_branch_workers(branch),
        "admin_id": get_branch_admin(branch),
        "schedule": get_schedule_status(branch),
    }


@app.post("/api/schedule")
def api_set_schedule(body: ScheduleIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_branch_admin(body.branch, x_init_data, x_site_token)
    if body.name not in get_branch_workers(body.branch):
        raise HTTPException(404, "Сотрудник не найден")
    if body.work_days <= 0 or body.rest_days < 0:
        raise HTTPException(400, "Некорректный график")
    set_worker_schedule(body.branch, body.name, body.work_days, body.rest_days, body.start_date)
    return {"ok": True, "schedule": get_schedule_status(body.branch)}


@app.delete("/api/schedule/{branch}/{name}")
def api_clear_schedule(branch: str, name: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_branch_admin(branch, x_init_data, x_site_token)
    clear_worker_schedule(branch, name)
    return {"ok": True, "schedule": get_schedule_status(branch)}


@app.get("/api/schedule/week")
def api_schedule_week(branch: str, monday: str = "", x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """График Пн–Пт для всех мойщиков филиала.
    monday — дата понедельника (YYYY-MM-DD); по умолчанию — понедельник текущей недели."""
    require_access(x_init_data, x_site_token)
    from datetime import date as _date, timedelta as _timedelta
    if monday:
        try:
            start = _date.fromisoformat(monday)
        except ValueError:
            raise HTTPException(400, "Некорректная дата")
    else:
        today = _date.today()
        start = today - _timedelta(days=today.weekday())

    days = [start + _timedelta(days=i) for i in range(7)]  # Пн..Вс
    day_labels = [d.strftime("%d.%m") for d in days]
    weekdays_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    workers = get_branch_workers(branch)
    rows = {}
    for w in workers:
        rows[w] = [is_working_on(branch, w, d) for d in days]

    return {
        "monday": start.isoformat(),
        "day_labels": day_labels,
        "weekday_labels": weekdays_ru,
        "workers": rows,
    }


@app.get("/api/me")
def api_me(branch: str = "", x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    user = auth_optional(x_init_data)
    uid = int(user.get("id", 0))
    users = load_users()
    employee_name = users.get(str(uid), "")
    is_worker = bool(employee_name) and branch and employee_name in get_branch_workers(branch)
    employee_roles = []
    if employee_name and branch:
        from employee_stats import get_branch_employee_roles
        employee_roles = get_branch_employee_roles(branch).get(employee_name, [])
    return {
        "user_id": uid,
        "name": user.get("first_name", ""),
        "is_owner": uid == OWNER_ID,
        "is_branch_admin": is_branch_admin(uid, branch) if branch else False,
        "employee_name": employee_name,
        "is_worker": is_worker,
        # Сотрудник в ЛЮБОЙ роли (мойщик и/или администратор и т.д.), не только мойщик —
        # используется, чтобы показывать "Моя смена" и админам-дежурным без роли мойщика.
        "is_employee": bool(employee_roles),
        "employee_roles": employee_roles,
    }


@app.post("/api/workers")
def api_add_worker(body: WorkerIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_branch_admin(body.branch, x_init_data, x_site_token)
    added = add_branch_worker(body.branch, body.name.strip())
    if not added:
        raise HTTPException(400, "Такой сотрудник уже есть")
    uid = find_user_id_by_name(body.name.strip())
    if uid:
        notify_user(uid, f"Вас добавили сотрудником в филиал «{body.branch}» ✅")
    return {"ok": True, "workers": get_branch_workers(body.branch)}


@app.delete("/api/workers/{branch}/{name}")
def api_remove_worker(branch: str, name: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_branch_admin(branch, x_init_data, x_site_token)
    remove_branch_worker(branch, name)
    return {"ok": True, "workers": get_branch_workers(branch)}


@app.post("/api/branch-admin")
def api_set_branch_admin(body: BranchAdminIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    site = get_site_session(x_site_token)
    if site:
        if site["role"] != "владелец" and not (site["role"] == "админ" and site.get("branch") == body.branch):
            raise HTTPException(403, "Нет прав")
    else:
        uid = current_user_id(x_init_data)
        if uid != OWNER_ID and not is_branch_admin(uid, body.branch):
            raise HTTPException(403, "Нет прав")
    set_branch_admin(body.branch, body.user_id)
    notify_user(body.user_id, f"Вас назначили администратором филиала «{body.branch}» 🛡️")
    return {"ok": True, "admin_id": get_branch_admin(body.branch)}


@app.get("/api/admins")
def api_list_admin_names(branch: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Ростер администраторов филиала (имена) + кто дежурит сегодня."""
    require_access(x_init_data, x_site_token)
    return {
        "admins": get_branch_admin_names(branch),
        "admin_on_duty": get_session_admin_name(branch),
    }


@app.post("/api/admins")
def api_add_admin_name(body: AdminNameIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_branch_admin(body.branch, x_init_data, x_site_token)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Укажите имя")
    added = add_branch_admin_name(body.branch, name)
    if not added:
        raise HTTPException(400, "Такой администратор уже есть")
    return {"ok": True, "admins": get_branch_admin_names(body.branch)}


@app.delete("/api/admins/{branch}/{name}")
def api_remove_admin_name(branch: str, name: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_branch_admin(branch, x_init_data, x_site_token)
    remove_branch_admin_name(branch, name)
    # если убрали дежурного администратора — снимаем и дежурство
    if get_session_admin_name(branch) == name:
        set_session_admin_name(branch, "")
    return {"ok": True, "admins": get_branch_admin_names(branch)}


@app.post("/api/admin-on-duty")
def api_set_admin_on_duty(body: AdminOnDutyIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_branch_admin(body.branch, x_init_data, x_site_token)
    name = body.name.strip()
    if name and name not in get_branch_admin_names(body.branch):
        raise HTTPException(404, "Этого администратора нет в списке филиала")
    set_session_admin_name(body.branch, name)
    return {"ok": True, "admin_on_duty": name}


@app.get("/api/admin-history")
def api_admin_history(branch: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Список архивных смен филиала (дата, кто дежурил сейчас проставлено,
    сколько машин/выручка) — для ручного заполнения истории задним числом,
    см. POST /api/admin-history."""
    require_branch_admin(branch, x_init_data, x_site_token)
    archive = load_archive()
    branch_archive = archive.get(branch, {})
    days = []
    for date_str, day in branch_archive.items():
        s = calculate_summary(day)
        days.append({
            "date": date_str,
            "admin_name": day.get("admin_name", ""),
            "cars": len(day.get("cars", [])),
            "revenue": s["total"],
            "admin_salary": s["admin_salary"],
        })
    try:
        days.sort(key=lambda d: datetime.strptime(d["date"], "%d.%m.%Y"), reverse=True)
    except ValueError:
        pass
    return {"branch": branch, "days": days, "admins": get_branch_admin_names(branch)}


@app.post("/api/admin-history")
def api_backfill_admin_history(body: AdminHistoryBackfillIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Проставить задним числом, кто дежурил администратором в конкретные
    уже закрытые смены — чтобы у истории зарплаты (/api/admin-stats)
    появились данные за дни ДО того, как это поле начали сохранять."""
    require_branch_admin(body.branch, x_init_data, x_site_token)
    roster = get_branch_admin_names(body.branch)
    updated, skipped = [], []
    for date_str, name in body.assignments.items():
        name = (name or "").strip()
        if name and name not in roster:
            skipped.append(date_str)
            continue
        ok = set_archive_admin_name(body.branch, date_str, name)
        (updated if ok else skipped).append(date_str)
    return {"ok": True, "updated": updated, "skipped": skipped}


@app.get("/api/users")
def api_list_users(x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_owner(x_init_data, x_site_token)
    users = load_users()
    return {"users": [{"user_id": int(uid), "name": name} for uid, name in users.items()]}


@app.post("/api/users")
def api_add_user(body: UserIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_owner(x_init_data, x_site_token)
    add_user(body.user_id, body.name)
    return {"ok": True}


@app.delete("/api/users/{user_id}")
def api_remove_user(user_id: int, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_owner(x_init_data, x_site_token)
    remove_user(user_id)
    return {"ok": True}


# ── Смена ────────────────────────────────────────────────────────────────
@app.get("/api/session")
def api_session(branch: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    session = get_session(branch)
    summary = calculate_summary(session)
    return {"session": session, "summary": summary}


def _compose_and_add_car(session: dict, branch: str, employee: str, body_type: str,
                          service_keys: list, custom_services: list,
                          price_override: int | None, payment: str, payment_split: dict | None,
                          comment: str, phone: str, client_name: str, car_label: str) -> tuple[dict, dict | None]:
    """Общая логика добавления машины в кассу смены — используется и прямым
    добавлением машины (/api/car), и автоматической конвертацией записи в
    машину при переводе записи в статус «Пришёл» (см. api_set_booking_status).
    Бросает HTTPException при ошибках валидации (нет услуг, некорректная сумма и т.д.)."""
    breakdown = {}
    for k in service_keys:
        if k not in SERVICES:
            continue
        breakdown[k] = {
            "name": SERVICES[k]["name"],
            "price": get_service_price(k, body_type),
            "percent": SERVICES[k]["percent"],
        }
    for i, c in enumerate(custom_services):
        breakdown[f"custom_{i}"] = {
            "name": c["name"], "price": int(c["price"]), "percent": float(c["percent"]) / 100,
        }

    if not breakdown:
        raise HTTPException(400, "Нужна хотя бы одна услуга")

    calc_price = sum(v["price"] for v in breakdown.values())
    total_price = calc_price
    if price_override is not None:
        if price_override < 0:
            raise HTTPException(400, "Итоговая сумма не может быть отрицательной")
        total_price = int(price_override)

    if payment_split:
        split_sum = sum(payment_split.values())
        if split_sum != total_price:
            raise HTTPException(400, f"Сумма раздельной оплаты ({split_sum}₽) не совпадает со стоимостью ({total_price}₽)")

    num = len(session["cars"]) + 1
    car = {
        "num": num,
        "employee": employee,
        "body_type": body_type,
        "service_keys": service_keys,
        "custom_services": custom_services,
        "price_breakdown": breakdown,
        "service": " + ".join(v["name"] for v in breakdown.values()),
        "price": total_price,
        "price_calc": calc_price,
        "price_override": total_price if total_price != calc_price else None,
        "car": car_label,
        "payment": payment or "нал",
        "payment_split": payment_split,
        "comment": comment,
        "status": "in_progress",
        "time": datetime.now().strftime("%H:%M"),
        "phone": normalize_phone(phone) if phone else "",
        "client_name": client_name,
    }
    session["cars"].append(car)
    save_sessions()
    client = None
    if phone:
        client = upsert_client_visit(
            phone, client_name, branch, car_label,
            total_price, car_num=num, service=car["service"], time=car["time"])
    return car, client


def _maybe_convert_booking_to_car(booking: dict, x_init_data: str, x_site_token: str) -> tuple[dict, int | None, str | None]:
    """Как только у записи выбрана хотя бы одна услуга и она ещё не
    конвертирована — создаёт машину в кассе ТЕКУЩЕЙ смены филиала на основе
    данных записи (услуги/сумма/оплата/клиент/мойщик), см. комментарий в
    sessions.py над разделом ЗАПИСИ. Идемпотентно: если у записи уже
    проставлен car_num, повторная машина не создаётся — дальнейшие правки
    записи вместо этого обновляют уже созданную машину через
    _sync_car_from_booking. Срабатывает при ЛЮБОМ статусе записи (booking,
    cars и касса должны быть связаны с момента сохранения записи, а не
    только при переводе в «Пришёл»). Вызывается из создания записи (POST),
    из полного PATCH записи и из отдельного PATCH .../status. Возвращает
    (актуальная_запись, car_created, car_note)."""
    car_created = None
    car_note = None
    has_services = bool(booking.get("service_keys") or booking.get("custom_services"))
    if has_services and not booking.get("car_num"):
        session = get_session(booking["branch"])
        if booking.get("date") != datetime.now().strftime("%d.%m.%Y"):
            car_note = "Запись не на сегодняшнюю смену — машина в кассу не добавлена автоматически"
        elif not session.get("day_open", True):
            car_note = "Смена ещё не открыта — машина в кассу не добавлена. Откройте смену и повторите"
        else:
            try:
                car, client = _compose_and_add_car(
                    session, booking["branch"], booking.get("employee", ""), booking.get("body_type", ""),
                    booking.get("service_keys", []), booking.get("custom_services", []),
                    booking.get("price_override"), booking.get("payment") or "нал", booking.get("payment_split"),
                    booking.get("comment", ""), booking.get("phone", ""), booking.get("client_name", ""),
                    booking.get("car", ""))
                for k in booking.get("product_keys", []):
                    product = PRODUCTS.get(k)
                    if not product:
                        continue
                    session.setdefault("products", []).append({
                        "key": k, "name": product["name"], "price": product["price"],
                        "payment": booking.get("payment") or "нал", "num": len(session["products"]) + 1,
                    })
                save_sessions()
                car_created = car["num"]
                log_action(booking["branch"], "add", current_user_id(x_init_data), current_user_name(x_init_data),
                           f"{car['car'] or 'машина'} · {car['service']} · {car['price']}₽ · из записи №{booking['id']}")
            except HTTPException as e:
                car_note = e.detail
    if car_created:
        booking = update_booking(booking["id"], car_num=car_created) or booking
    return booking, car_created, car_note


def _sync_car_from_booking(booking: dict) -> str | None:
    """Если запись уже конвертирована в машину (car_num проставлен), при
    каждой дальнейшей правке записи подтягивает изменения (услуги/сумма/
    оплата/мойщик/клиент) в уже созданную машину кассы — чтобы booking,
    cars и касса оставались связаны и после первого сохранения, а не только
    в момент создания. Молча ничего не делает, если машина не найдена (её
    могли удалить из кассы вручную) или если из записи убрали все услуги
    (тогда машину в кассе трогать не стоит — её можно поправить руками).
    Возвращает car_note при ошибке синхронизации, иначе None."""
    car_num = booking.get("car_num")
    if not car_num:
        return None
    session = get_session(booking["branch"])
    car = next((c for c in session.get("cars", []) if c["num"] == car_num), None)
    if not car:
        return None

    breakdown = _rebuild_car_breakdown(
        booking.get("body_type", ""), booking.get("service_keys", []), booking.get("custom_services", []))
    if not breakdown:
        return None

    calc_price = sum(v["price"] for v in breakdown.values())
    price_override = booking.get("price_override")
    total_price = int(price_override) if price_override is not None else calc_price
    payment_split = booking.get("payment_split") or None
    if payment_split:
        split_sum = sum(payment_split.values())
        if split_sum != total_price:
            return f"Сумма раздельной оплаты записи ({split_sum}₽) не совпадает со стоимостью ({total_price}₽) — машина в кассе не обновлена"

    car["employee"] = booking.get("employee", car.get("employee", ""))
    car["body_type"] = booking.get("body_type", car.get("body_type", ""))
    car["service_keys"] = booking.get("service_keys", [])
    car["custom_services"] = booking.get("custom_services", [])
    car["price_breakdown"] = breakdown
    car["service"] = " + ".join(v["name"] for v in breakdown.values())
    car["price_calc"] = calc_price
    car["price"] = total_price
    car["price_override"] = total_price if total_price != calc_price else None
    car["car"] = booking.get("car", car.get("car", ""))
    car["payment"] = booking.get("payment") or car.get("payment", "нал")
    car["payment_split"] = payment_split
    car["comment"] = booking.get("comment", car.get("comment", ""))

    new_phone = normalize_phone(booking.get("phone", "")) if booking.get("phone") else ""
    old_phone = car.get("phone", "")
    car["phone"] = new_phone
    car["client_name"] = booking.get("client_name", car.get("client_name", ""))
    if new_phone and new_phone != old_phone:
        upsert_client_visit(new_phone, booking.get("client_name", ""), booking["branch"], car.get("car", ""),
                             total_price, car_num=car_num, service=car["service"], time=car.get("time", ""))
    elif new_phone and booking.get("client_name"):
        update_client(new_phone, name=booking.get("client_name"))

    save_sessions()
    return None


@app.post("/api/car")
def api_add_car(body: CarIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    session = get_session(body.branch)
    if not session.get("day_open", True):
        raise HTTPException(403, "Смена ещё не открыта. Попросите администратора нажать «Открыть смену».")
    car, client = _compose_and_add_car(
        session, body.branch, body.employee, body.body_type,
        body.service_keys, body.custom_services,
        body.price_override, body.payment, body.payment_split,
        body.comment, body.phone, body.client_name, body.car)
    log_action(body.branch, "add", current_user_id(x_init_data), current_user_name(x_init_data),
               f"{car['car'] or 'машина'} · {car['service']} · {car['price']}₽")
    return {"ok": True, "car": car, "summary": calculate_summary(session), "client": client}


# ── КЛИЕНТЫ (карточка, поиск/автодополнение по телефону или имени) ────────

@app.get("/api/clients/search")
def api_search_clients(q: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Автодополнение при вводе телефона/имени клиента в форме добавления
    машины — и на сайте, и в mini-app, и в боте."""
    require_access(x_init_data, x_site_token)
    return {"clients": search_clients(q)}


@app.get("/api/clients/{phone}")
def api_get_client(phone: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    client = find_client(phone)
    if not client:
        raise HTTPException(404, "Клиент не найден")
    return client


@app.get("/api/clients")
def api_list_clients(x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Полный список клиентов сети (для страницы «Клиенты»), отсортирован
    по дате последнего визита — самые недавние сверху."""
    require_access(x_init_data, x_site_token)
    clients = [client_summary(c) for c in load_clients().values()]
    clients.sort(key=lambda c: datetime.strptime(c["last_visit"], "%d.%m.%Y") if c.get("last_visit") else datetime.min,
                 reverse=True)
    return {"clients": clients, "total": len(clients)}


@app.put("/api/clients/{phone}")
def api_update_client(phone: str, body: ClientUpdateIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Редактирование карточки клиента (имя, список машин) вручную со
    страницы «Клиенты». Визиты и телефон этим не затрагиваются."""
    require_access(x_init_data, x_site_token)
    client = update_client(phone, name=body.name, cars=body.cars)
    if not client:
        raise HTTPException(404, "Клиент не найден")
    return client


@app.put("/api/clients/{phone}/discount")
def api_set_client_discount(phone: str, body: ClientDiscountIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Устанавливает постоянную скидку клиента на все услуги (0 < percent
    <= 100), как в блоке «Постоянная скидка» в макете модалки."""
    require_access(x_init_data, x_site_token)
    if body.percent <= 0 or body.percent > 100:
        raise HTTPException(400, "Скидка должна быть в диапазоне от 0 до 100%")
    client = set_client_discount(phone, body.percent)
    if not client:
        raise HTTPException(404, "Клиент не найден")
    log_action("—", "client_discount", current_user_id(x_init_data), current_user_name(x_init_data),
               f"{client.get('name') or phone} · скидка {body.percent}%")
    return client


@app.delete("/api/clients/{phone}/discount")
def api_clear_client_discount(phone: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    client = clear_client_discount(phone)
    if not client:
        raise HTTPException(404, "Клиент не найден")
    log_action("—", "client_discount", current_user_id(x_init_data), current_user_name(x_init_data),
               f"{client.get('name') or phone} · скидка снята")
    return client


def _rebuild_car_breakdown(body_type: str, service_keys: list, custom_services: list) -> dict:
    breakdown = {}
    for k in service_keys:
        if k not in SERVICES:
            continue
        breakdown[k] = {
            "name": SERVICES[k]["name"],
            "price": get_service_price(k, body_type),
            "percent": SERVICES[k]["percent"],
        }
    for i, c in enumerate(custom_services):
        breakdown[f"custom_{i}"] = {
            "name": c["name"], "price": int(c["price"]), "percent": float(c["percent"]) / 100,
        }
    return breakdown


@app.put("/api/car/{branch}/{num}")
def api_edit_car(branch: str, num: int, body: CarEditIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Редактирование существующей машины (услуги/оплата/мойщик и т.д.),
    вместо удаления и создания заново."""
    require_access(x_init_data, x_site_token)
    session = get_session(branch)
    car = next((c for c in session["cars"] if c["num"] == num), None)
    if not car:
        raise HTTPException(404, "Машина не найдена")

    if body.employee is not None:
        car["employee"] = body.employee
    if body.car is not None:
        car["car"] = body.car
    if body.comment is not None:
        car["comment"] = body.comment
    if body.payment is not None:
        car["payment"] = body.payment
    if body.payment_split is not None:
        car["payment_split"] = body.payment_split or None

    if body.body_type is not None or body.service_keys is not None or body.custom_services is not None:
        body_type = body.body_type or car["body_type"]
        service_keys = body.service_keys if body.service_keys is not None else car["service_keys"]
        custom_services = body.custom_services if body.custom_services is not None else car["custom_services"]
        breakdown = _rebuild_car_breakdown(body_type, service_keys, custom_services)
        if not breakdown:
            raise HTTPException(400, "Нужна хотя бы одна услуга")
        car["body_type"] = body_type
        car["service_keys"] = service_keys
        car["custom_services"] = custom_services
        car["price_breakdown"] = breakdown
        car["service"] = " + ".join(v["name"] for v in breakdown.values())
        car["price_calc"] = sum(v["price"] for v in breakdown.values())
        if car.get("price_override") is not None and not body.clear_price_override and body.price_override is None:
            pass  # ручная цена сохраняется при смене состава услуг, пока её явно не сбросили
        else:
            car["price"] = car["price_calc"]
            car["price_override"] = None

    if body.clear_price_override:
        car["price"] = car.get("price_calc", car["price"])
        car["price_override"] = None
    elif body.price_override is not None:
        if body.price_override < 0:
            raise HTTPException(400, "Итоговая сумма не может быть отрицательной")
        car["price"] = int(body.price_override)
        car["price_override"] = int(body.price_override)

    # телефон/имя клиента: если номер указывается впервые — это как обычное
    # добавление визита (с уже финальной ценой); если номер тот же — просто
    # обновляем имя без повторной записи визита (иначе визит задвоился бы
    # при каждой правке)
    if body.phone is not None:
        new_phone = normalize_phone(body.phone) if body.phone else ""
        old_phone = car.get("phone", "")
        car["phone"] = new_phone
        if new_phone and new_phone != old_phone:
            upsert_client_visit(new_phone, body.client_name or "", branch, car.get("car", ""), car["price"],
                                 car_num=num, service=car.get("service", ""), time=car.get("time", ""))
        elif new_phone and body.client_name:
            update_client(new_phone, name=body.client_name)
    elif body.client_name and car.get("phone"):
        update_client(car["phone"], name=body.client_name)

    if car.get("payment_split"):
        split_sum = sum(car["payment_split"].values())
        if split_sum != car["price"]:
            raise HTTPException(400, f"Сумма раздельной оплаты ({split_sum}₽) не совпадает со стоимостью ({car['price']}₽)")

    save_sessions()
    log_action(branch, "edit", current_user_id(x_init_data), current_user_name(x_init_data),
               f"{car['car'] or 'машина'} · {car['service']} · {car['price']}₽")
    return {"ok": True, "car": car, "summary": calculate_summary(session)}


@app.patch("/api/car/{branch}/{num}/status")
def api_set_car_status(branch: str, num: int, body: CarStatusIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Переключение статуса 'в работе' / 'оплачено'. Это отметка для персонала —
    на кассу и расчёты никак не влияет (машина учитывается в кассе сразу при добавлении)."""
    require_access(x_init_data, x_site_token)
    if body.status not in ("in_progress", "done"):
        raise HTTPException(400, "Статус может быть 'in_progress' или 'done'")
    session = get_session(branch)
    car = next((c for c in session["cars"] if c["num"] == num), None)
    if not car:
        raise HTTPException(404, "Машина не найдена")
    car["status"] = body.status
    save_sessions()
    log_action(branch, "status", current_user_id(x_init_data), current_user_name(x_init_data),
               f"{car.get('car') or 'машина'} · статус → {'оплачено' if body.status=='done' else 'в работе'}")
    return {"ok": True, "car": car}


@app.delete("/api/car/{branch}/{num}")
def api_delete_car(branch: str, num: int, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    session = get_session(branch)
    car = next((c for c in session["cars"] if c["num"] == num), None)
    session["cars"] = [c for c in session["cars"] if c["num"] != num]
    save_sessions()
    if car:
        log_action(branch, "delete", current_user_id(x_init_data), current_user_name(x_init_data),
                   f"{car.get('car') or 'машина'} · {car.get('service','')} · {car.get('price',0)}₽")
    return {"ok": True, "summary": calculate_summary(session)}



# ── ЗАПИСЬ (ЖУРНАЛ ЗАПИСИ / BOOKINGS) ───────────────────────────────────────
# Новый раздел, см. 00-audit-i-plan.md и sessions.py. В отличие от /api/car
# (машина сразу попадает в кассу дня), запись — это слот в боксе на
# дату/время, который ещё может не иметь ни одной выбранной услуги (клиента
# просто записали на время), поэтому в отличие от api_add_car здесь НЕ
# требуется хотя бы одна услуга.

def _booking_breakdown(body_type: str, service_keys: list, custom_services: list, product_keys: list) -> dict:
    breakdown = {}
    for k in service_keys:
        if k not in SERVICES:
            continue
        breakdown[k] = {
            "name": SERVICES[k]["name"],
            "price": get_service_price(k, body_type or "sedan"),
            "percent": SERVICES[k]["percent"],
        }
    for i, c in enumerate(custom_services):
        breakdown[f"custom_{i}"] = {
            "name": c["name"], "price": int(c["price"]), "percent": float(c.get("percent", 0)) / 100,
        }
    for k in product_keys:
        if k not in PRODUCTS:
            continue
        breakdown[f"product_{k}"] = {
            "name": PRODUCTS[k]["name"], "price": PRODUCTS[k]["price"], "percent": 0,
        }
    return breakdown


@app.get("/api/bookings")
def api_list_bookings(branch: str, date: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Записи филиала на дату (ДД.ММ.ГГГГ) + список боксов (= сотрудники
    филиала по порядку, см. get_branch_boxes). У каждого бокса отдаётся
    on_duty — работает ли сотрудник именно в эту дату по графику, чтобы
    страница могла по умолчанию показывать только тех, кто на смене."""
    require_access(x_init_data, x_site_token)
    try:
        on_date = datetime.strptime(date, "%d.%m.%Y").date()
    except ValueError:
        on_date = None
    return {"bookings": get_bookings(branch, date), "boxes": get_branch_boxes(branch, on_date)}


@app.post("/api/bookings")
def api_create_booking(body: BookingIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    if body.status not in BOOKING_STATUSES:
        raise HTTPException(400, f"Недопустимый статус. Разрешены: {', '.join(sorted(BOOKING_STATUSES))}")
    if body.box <= 0:
        raise HTTPException(400, "Некорректный номер бокса")
    if body.start_time >= body.end_time:
        raise HTTPException(400, "Время начала должно быть раньше времени окончания")

    conflict = find_conflicting_booking(body.branch, body.date, body.box, body.start_time, body.end_time)
    if conflict:
        raise HTTPException(409, f"Бокс {body.box} занят с {conflict['start_time']} до {conflict['end_time']} "
                                  f"({conflict.get('client_name') or conflict.get('car') or 'без имени'})")

    breakdown = _booking_breakdown(body.body_type, body.service_keys, body.custom_services, body.product_keys)
    calc_price = sum(v["price"] for v in breakdown.values())
    total_price = calc_price
    if body.price_override is not None:
        if body.price_override < 0:
            raise HTTPException(400, "Итоговая сумма не может быть отрицательной")
        total_price = int(body.price_override)
    if body.payment_split:
        split_sum = sum(body.payment_split.values())
        if split_sum != total_price:
            raise HTTPException(400, f"Сумма раздельной оплаты ({split_sum}₽) не совпадает со стоимостью ({total_price}₽)")

    booking = create_booking(
        branch=body.branch, date=body.date, box=body.box,
        start_time=body.start_time, end_time=body.end_time,
        employee=body.employee, body_type=body.body_type, car=body.car,
        service_keys=body.service_keys, custom_services=body.custom_services, product_keys=body.product_keys,
        price=total_price, price_calc=calc_price,
        price_override=total_price if body.price_override is not None else None,
        payment=body.payment, payment_split=body.payment_split, comment=body.comment,
        phone=body.phone, client_name=body.client_name, status=body.status,
    )
    log_action(body.branch, "booking_add", current_user_id(x_init_data), current_user_name(x_init_data),
               f"бокс {body.box} · {body.start_time}–{body.end_time} · {body.client_name or body.car or 'без имени'}")
    booking, car_created, car_note = _maybe_convert_booking_to_car(booking, x_init_data, x_site_token)
    return {"ok": True, "booking": booking, "car_created": car_created, "car_note": car_note}


@app.patch("/api/bookings/{booking_id}")
def api_edit_booking(booking_id: int, body: BookingEditIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    booking = get_booking(booking_id)
    if not booking:
        raise HTTPException(404, "Запись не найдена")

    if body.status is not None and body.status not in BOOKING_STATUSES:
        raise HTTPException(400, f"Недопустимый статус. Разрешены: {', '.join(sorted(BOOKING_STATUSES))}")

    target_branch = body.branch if body.branch is not None else booking["branch"]
    target_date = body.date if body.date is not None else booking["date"]
    target_box = body.box if body.box is not None else booking["box"]
    target_start = body.start_time if body.start_time is not None else booking["start_time"]
    target_end = body.end_time if body.end_time is not None else booking["end_time"]
    if target_start >= target_end:
        raise HTTPException(400, "Время начала должно быть раньше времени окончания")
    if body.box is not None or body.start_time is not None or body.end_time is not None or \
       body.branch is not None or body.date is not None:
        conflict = find_conflicting_booking(target_branch, target_date, target_box, target_start, target_end,
                                             exclude_id=booking_id)
        if conflict:
            raise HTTPException(409, f"Бокс {target_box} занят с {conflict['start_time']} до {conflict['end_time']} "
                                      f"({conflict.get('client_name') or conflict.get('car') or 'без имени'})")

    fields: dict = {
        "branch": body.branch, "date": body.date, "box": body.box,
        "start_time": body.start_time, "end_time": body.end_time,
        "employee": body.employee, "car": body.car, "comment": body.comment,
        "payment": body.payment, "phone": normalize_phone(body.phone) if body.phone else body.phone,
        "client_name": body.client_name, "status": body.status,
    }
    if body.payment_split is not None:
        fields["payment_split"] = body.payment_split or None

    if body.body_type is not None or body.service_keys is not None or \
       body.custom_services is not None or body.product_keys is not None:
        body_type = body.body_type if body.body_type is not None else booking["body_type"]
        service_keys = body.service_keys if body.service_keys is not None else booking["service_keys"]
        custom_services = body.custom_services if body.custom_services is not None else booking["custom_services"]
        product_keys = body.product_keys if body.product_keys is not None else booking.get("product_keys", [])
        breakdown = _booking_breakdown(body_type, service_keys, custom_services, product_keys)
        fields["body_type"] = body_type
        fields["service_keys"] = service_keys
        fields["custom_services"] = custom_services
        fields["product_keys"] = product_keys
        fields["price_calc"] = sum(v["price"] for v in breakdown.values())
        if booking.get("price_override") is not None and not body.clear_price_override and body.price_override is None:
            pass  # ручная цена сохраняется при смене состава услуг, пока её явно не сбросили
        else:
            fields["price"] = fields["price_calc"]
            fields["price_override"] = None

    if body.clear_price_override:
        fields["price"] = fields.get("price_calc", booking.get("price_calc", booking["price"]))
        fields["price_override"] = None
    elif body.price_override is not None:
        if body.price_override < 0:
            raise HTTPException(400, "Итоговая сумма не может быть отрицательной")
        fields["price"] = int(body.price_override)
        fields["price_override"] = int(body.price_override)

    # Валидация суммы раздельной оплаты — ДО записи изменений на диск (иначе
    # при несовпадении сумм невалидные данные успевают сохраниться в
    # carwash_bookings.json до того, как эндпоинт вернёт 400).
    final_price = fields["price"] if fields.get("price") is not None else booking.get("price", 0)
    final_split = fields["payment_split"] if "payment_split" in fields else booking.get("payment_split")
    if final_split:
        split_sum = sum(final_split.values())
        if split_sum != final_price:
            raise HTTPException(400, f"Сумма раздельной оплаты ({split_sum}₽) не совпадает со стоимостью ({final_price}₽)")

    updated = update_booking(booking_id, **fields)

    log_action(updated["branch"], "booking_edit", current_user_id(x_init_data), current_user_name(x_init_data),
               f"бокс {updated['box']} · {updated['start_time']}–{updated['end_time']}")
    updated, car_created, car_note = _maybe_convert_booking_to_car(updated, x_init_data, x_site_token)
    if not car_created:
        sync_note = _sync_car_from_booking(updated)
        if sync_note:
            car_note = sync_note
    return {"ok": True, "booking": updated, "car_created": car_created, "car_note": car_note}


@app.patch("/api/bookings/{booking_id}/status")
def api_set_booking_status(booking_id: int, body: BookingStatusIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    if body.status not in BOOKING_STATUSES:
        raise HTTPException(400, f"Недопустимый статус. Разрешены: {', '.join(sorted(BOOKING_STATUSES))}")
    booking = get_booking(booking_id)
    if not booking:
        raise HTTPException(404, "Запись не найдена")
    updated = set_booking_status(booking_id, body.status)
    updated, car_created, car_note = _maybe_convert_booking_to_car(updated, x_init_data, x_site_token)
    if not car_created:
        sync_note = _sync_car_from_booking(updated)
        if sync_note:
            car_note = sync_note
    log_action(updated["branch"], "booking_status", current_user_id(x_init_data), current_user_name(x_init_data),
               f"бокс {updated['box']} · статус → {body.status}")
    return {"ok": True, "booking": updated, "car_created": car_created, "car_note": car_note}


@app.delete("/api/bookings/{booking_id}")
def api_delete_booking(booking_id: int, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    booking = get_booking(booking_id)
    if booking:
        delete_booking(booking_id)
        log_action(booking["branch"], "booking_delete", current_user_id(x_init_data), current_user_name(x_init_data),
                   f"бокс {booking['box']} · {booking['start_time']}–{booking['end_time']}")
    return {"ok": True}


@app.post("/api/loyalty")
def api_add_loyalty(body: LoyaltyIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    session = get_session(body.branch)
    session.setdefault("loyalty", []).append({"car_num": body.car_num, "discount": body.discount})
    save_sessions()
    return {"ok": True, "summary": calculate_summary(session)}


@app.delete("/api/loyalty/{branch}/{idx}")
def api_delete_loyalty(branch: str, idx: int, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    session = get_session(branch)
    loyalty = session.get("loyalty", [])
    if not (0 <= idx < len(loyalty)):
        raise HTTPException(404, "Скидка не найдена")
    removed = loyalty.pop(idx)
    save_sessions()
    log_action(branch, "loyalty_delete", current_user_id(x_init_data), current_user_name(x_init_data),
               f"машина №{removed.get('car_num')} · -{removed.get('discount',0)}₽")
    return {"ok": True, "summary": calculate_summary(session)}


@app.post("/api/expense")
def api_add_expense(body: ExpenseIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    session = get_session(body.branch)
    session.setdefault("expenses", []).append({"name": body.name, "amount": body.amount})
    save_sessions()
    log_action(body.branch, "expense_add", current_user_id(x_init_data), current_user_name(x_init_data),
               f"{body.name} · -{body.amount}₽")
    return {"ok": True, "summary": calculate_summary(session)}


@app.delete("/api/expense/{branch}/{idx}")
def api_delete_expense(branch: str, idx: int, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    session = get_session(branch)
    expenses = session.get("expenses", [])
    if not (0 <= idx < len(expenses)):
        raise HTTPException(404, "Расход не найден")
    removed = expenses.pop(idx)
    save_sessions()
    log_action(branch, "expense_delete", current_user_id(x_init_data), current_user_name(x_init_data),
               f"{removed['name']} · -{removed['amount']}₽")
    return {"ok": True, "summary": calculate_summary(session)}


@app.post("/api/income")
def api_add_income(body: IncomeIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    session = get_session(body.branch)
    entry = {"name": body.name, "amount": body.amount}
    if body.payment_split:
        entry["payment_split"] = body.payment_split
    else:
        entry["payment"] = body.payment
    session.setdefault("incomes", []).append(entry)
    save_sessions()
    log_action(body.branch, "income_add", current_user_id(x_init_data), current_user_name(x_init_data),
               f"{body.name} · +{body.amount}₽")
    return {"ok": True, "summary": calculate_summary(session)}


@app.delete("/api/income/{branch}/{idx}")
def api_delete_income(branch: str, idx: int, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    session = get_session(branch)
    incomes = session.get("incomes", [])
    if not (0 <= idx < len(incomes)):
        raise HTTPException(404, "Доход не найден")
    removed = incomes.pop(idx)
    save_sessions()
    log_action(branch, "income_delete", current_user_id(x_init_data), current_user_name(x_init_data),
               f"{removed['name']} · +{removed['amount']}₽")
    return {"ok": True, "summary": calculate_summary(session)}


class FixedRateIn(BaseModel):
    branch: str
    worker: str
    amount: int


@app.post("/api/fixed-rate")
def api_set_fixed_rate(body: FixedRateIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Ставка — фиксированная зарплата за день (например, если мойщик не помыл
    ни одной машины). Добавляется к зарплате сверх расчёта по машинам."""
    require_branch_admin(body.branch, x_init_data, x_site_token)
    if body.worker not in get_branch_workers(body.branch):
        raise HTTPException(404, "Сотрудник не найден в этом филиале")
    if body.amount <= 0:
        raise HTTPException(400, "Сумма ставки должна быть больше нуля")
    session = get_session(body.branch)
    session.setdefault("fixed_rates", {})[body.worker] = body.amount
    save_sessions()
    log_action(body.branch, "fixed_rate_set", current_user_id(x_init_data), current_user_name(x_init_data),
               f"{body.worker} · ставка {body.amount}₽")
    return {"ok": True, "summary": calculate_summary(session)}


@app.delete("/api/fixed-rate/{branch}/{worker}")
def api_clear_fixed_rate(branch: str, worker: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_branch_admin(branch, x_init_data, x_site_token)
    session = get_session(branch)
    rates = session.get("fixed_rates", {})
    if worker not in rates:
        raise HTTPException(404, "Ставка не установлена")
    removed = rates.pop(worker)
    save_sessions()
    log_action(branch, "fixed_rate_clear", current_user_id(x_init_data), current_user_name(x_init_data),
               f"{worker} · убрана ставка {removed}₽")
    return {"ok": True, "summary": calculate_summary(session)}


class AdminFixedRateIn(BaseModel):
    branch: str
    amount: int = 1000


@app.post("/api/admin-fixed-rate")
def api_set_admin_fixed_rate(body: AdminFixedRateIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Ставка администратора — та же логика, что и ставка мойщика (см. выше),
    но для роли "администратор": позволяет закрыть ПУСТОЙ отчёт (за смену не
    было ни одной машины), при этом администратор всё равно получает фикс
    (по умолчанию 1000₽), не привязанный к проценту с выручки."""
    require_branch_admin(body.branch, x_init_data, x_site_token)
    if body.amount <= 0:
        raise HTTPException(400, "Сумма ставки должна быть больше нуля")
    session = get_session(body.branch)
    if not session.get("admin_name"):
        raise HTTPException(400, "Сначала укажите администратора смены")
    session["admin_fixed_rate"] = body.amount
    save_sessions()
    log_action(body.branch, "admin_fixed_rate_set", current_user_id(x_init_data), current_user_name(x_init_data),
               f"{session['admin_name']} · ставка администратора {body.amount}₽")
    return {"ok": True, "summary": calculate_summary(session)}


@app.delete("/api/admin-fixed-rate/{branch}")
def api_clear_admin_fixed_rate(branch: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_branch_admin(branch, x_init_data, x_site_token)
    session = get_session(branch)
    removed = session.pop("admin_fixed_rate", 0)
    if not removed:
        raise HTTPException(404, "Ставка администратора не установлена")
    save_sessions()
    log_action(branch, "admin_fixed_rate_clear", current_user_id(x_init_data), current_user_name(x_init_data),
               f"убрана ставка администратора {removed}₽")
    return {"ok": True, "summary": calculate_summary(session)}


@app.post("/api/product")
def api_add_product(body: ProductIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Товар из каталога (см. config.PRODUCTS) — в отличие от 'Доходов', сумма
    товаров учитывается в базе для расчёта зарплаты администратора
    (мойка + товары), см. calculator.py."""
    require_access(x_init_data, x_site_token)
    product = PRODUCTS.get(body.key)
    if not product:
        raise HTTPException(404, "Товар не найден в каталоге")
    session = get_session(body.branch)
    entry = {
        "key": body.key, "name": product["name"], "price": product["price"],
        "payment": body.payment, "num": len(session.get("products", [])) + 1,
    }
    session.setdefault("products", []).append(entry)
    save_sessions()
    log_action(body.branch, "product_add", current_user_id(x_init_data), current_user_name(x_init_data),
               f"{entry['name']} · {entry['price']}₽")
    return {"ok": True, "summary": calculate_summary(session)}


@app.delete("/api/product/{branch}/{num}")
def api_delete_product(branch: str, num: int, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    session = get_session(branch)
    products = session.get("products", [])
    product = next((p for p in products if p.get("num") == num), None)
    if not product:
        raise HTTPException(404, "Товар не найден")
    products.remove(product)
    save_sessions()
    log_action(branch, "product_delete", current_user_id(x_init_data), current_user_name(x_init_data),
               f"{product['name']} · {product['price']}₽")
    return {"ok": True, "summary": calculate_summary(session)}


# ── Отчёты ───────────────────────────────────────────────────────────────
@app.get("/api/archive")
def api_archive(branch: str, limit: int = 14, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    archive = load_archive()
    branch_archive = archive.get(branch, {})  # {"04.07.2026": {...cars, products, ...}, ...}

    days = []
    for date_str, day in branch_archive.items():
        try:
            dt = datetime.strptime(date_str, "%d.%m.%Y")
        except ValueError:
            continue
        days.append((dt, {"date": date_str, **day}))

    days.sort(key=lambda pair: pair[0], reverse=True)  # сначала самые свежие
    return {"days": [d for _, d in days[:limit]]}


class NewDayIn(BaseModel):
    actual_cash: Optional[int] = None  # сколько реально насчитали в кассе (наличные), для сверки


def _do_close_day(branch: str, body: "NewDayIn", x_init_data: str, x_site_token: str):
    """Общая логика закрытия смены: используется и мини-аппой (/api/newday),
    и сайтом (/api/closeday) — раньше это было продублировано, теперь один
    источник правды."""
    session = get_session(branch)
    from sessions import session_has_data
    had_data = session_has_data(session)

    discrepancy = None
    expected_cash = None
    if had_data and body.actual_cash is not None:
        summary = calculate_summary(session)
        expected_cash = summary["cash"]
        discrepancy = body.actual_cash - expected_cash
        session["actual_cash"] = body.actual_cash
        session["cash_discrepancy"] = discrepancy

    if had_data:
        save_to_archive(branch, session)

    actor_id, actor_name = current_user_id(x_init_data), current_user_name(x_init_data)
    if discrepancy is not None and discrepancy != 0:
        sign = "недостача" if discrepancy < 0 else "излишек"
        log_action(branch, "newday", actor_id, actor_name,
                   f"Смена закрыта · касса не сошлась: {sign} {abs(discrepancy)}₽ "
                   f"(в системе {expected_cash}₽, по факту {body.actual_cash}₽)")
    else:
        log_action(branch, "newday", actor_id, actor_name, "Смена закрыта")

    reset_session(branch)  # новая пустая смена: day_open=False, нужно явно открыть
    return {"ok": True, "discrepancy": discrepancy}


@app.post("/api/newday")
def api_newday(branch: str, body: Optional[NewDayIn] = None,
               x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_branch_admin(branch, x_init_data, x_site_token)
    return _do_close_day(branch, body or NewDayIn(), x_init_data, x_site_token)


# ── Открытие/закрытие смены (кнопки на странице «Касса за смену») ─────────
@app.get("/api/dayopen")
def api_dayopen(branch: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    session = get_session(branch)
    return {"open": session.get("day_open", True), "date": session.get("date")}


@app.post("/api/openday")
def api_openday(branch: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_branch_admin(branch, x_init_data, x_site_token)
    from sessions import open_day
    open_day(branch)
    actor_id, actor_name = current_user_id(x_init_data), current_user_name(x_init_data)
    log_action(branch, "dayopen", actor_id, actor_name, "Смена открыта")
    return {"ok": True}


@app.post("/api/closeday")
def api_closeday(branch: str, body: Optional[NewDayIn] = None,
                  x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_branch_admin(branch, x_init_data, x_site_token)
    return _do_close_day(branch, body or NewDayIn(), x_init_data, x_site_token)


@app.get("/api/day-summary")
def api_day_summary(branch: str, date: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Свод по кассе + записям за конкретный день (ДД.ММ.ГГГГ) — данные для
    попапа «Итоги дня» на странице «Запись» (booking.html), который должен
    быть синхронизирован с реальными цифрами кассы/машин, а не показывать
    отдельную, ничем не связанную статистику.

    Для сегодняшней (ещё не закрытой) смены берёт живую сессию
    (get_session) — те же данные, что показывает /api/reports/today.
    Для прошлых дней — архив (туда данные попадают при закрытии смены,
    см. save_to_archive/_do_close_day). Если день ещё не закрыт и это не
    сегодня (или касса в этот день вообще не велась/будущая дата) —
    кассовые показатели будут нулевыми: bookings — независимый источник
    данных и считаются всегда, даже если кассы за этот день ещё/уже нет."""
    require_access(x_init_data, x_site_token)

    today_key = datetime.now().strftime("%d.%m.%Y")
    is_live = (date == today_key)
    day_data = get_session(branch) if is_live else load_archive().get(branch, {}).get(date)

    if day_data:
        summary = calculate_summary(day_data)
        clients = len(day_data.get("cars", []))
        receipts_total = summary["total"]
        cash = summary["cash"]
        noncash = summary["visa"] + summary["beznal"]
        loyalty_total = summary["total_loyalty"]
        products_total = summary["total_products"]
    else:
        clients = receipts_total = cash = noncash = loyalty_total = products_total = 0

    bookings = [b for b in get_bookings(branch, date) if b.get("status") != "no_show"]
    bookings_total = sum(b.get("price", 0) for b in bookings)

    return {
        "date": date,
        "is_live": is_live,
        "has_cash_data": bool(day_data),
        "clients": clients,
        "receipts_total": receipts_total,  # «Поступлений в кассу»
        "cash": cash,                       # «Оплата наличными»
        "noncash": noncash,                 # «Оплата безналом» (visa+безнал вместе)
        "done_total": receipts_total,       # «Выполнено на сумму» — фактически поступившие деньги
        "bookings_total": bookings_total,   # «Записей на сумму» — сумма всех записей на день (в т.ч. не оплаченных)
        "bookings_count": len(bookings),
        "loyalty_total": loyalty_total,     # «Лояльность на сумму»
        "products_total": products_total,   # «Товаров на сумму»
    }


@app.get("/api/reports/today")
def api_report_today(branch: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    session = get_session(branch)
    summary = calculate_summary(session)
    svc_count = {}
    for c in session["cars"]:
        svc_count[c.get("service", "—")] = svc_count.get(c.get("service", "—"), 0) + 1
    top = sorted(svc_count.items(), key=lambda x: x[1], reverse=True)[:5]
    return {"session": session, "summary": summary, "top_services": top}


@app.get("/api/reports/week")
def api_report_week(branch: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    archive = load_archive()
    branch_archive = archive.get(branch, {})
    today = datetime.now()
    week_start = (today - timedelta(days=today.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)

    days = []
    grand = 0
    for date_str, day in branch_archive.items():
        try:
            day_dt = datetime.strptime(date_str, "%d.%m.%Y")
        except ValueError:
            continue
        if not (week_start <= day_dt <= today):
            continue
        s = calculate_summary(day)
        grand += s["grand_total"]
        days.append({"date": date_str, "cars": len(day.get("cars", [])), "total": s["grand_total"],
                     "washer_salaries": s["washer_salaries"]})

    session = get_session(branch)
    if session.get("cars"):
        s = calculate_summary(session)
        grand += s["grand_total"]
        days.append({"date": session.get("date"), "cars": len(session["cars"]), "total": s["grand_total"],
                     "washer_salaries": s["washer_salaries"]})

    days.sort(key=lambda d: datetime.strptime(d["date"], "%d.%m.%Y"))
    return {"from": week_start.strftime("%d.%m.%Y"), "to": today.strftime("%d.%m.%Y"),
            "grand_total": grand, "days": days}


@app.get("/api/reports/month")
def api_report_month(branch: str, month: str, year: int = 0, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    month_num = MONTHS_RU.get(month.lower())
    if not month_num:
        raise HTTPException(400, f"Не понял месяц '{month}'")
    year = year or datetime.now().year
    archive = load_archive()
    branch_archive = archive.get(branch, {})
    week_sal: dict[str, dict[int, int]] = {}
    grand = 0
    for date_str, day in branch_archive.items():
        try:
            dt = datetime.strptime(date_str, "%d.%m.%Y")
        except ValueError:
            continue
        if dt.month != month_num or dt.year != year:
            continue
        wk = (dt.day - 1) // 7 + 1
        s = calculate_summary(day)
        grand += s["grand_total"]
        for emp, sal in s["washer_salaries"].items():
            week_sal.setdefault(emp, {})
            week_sal[emp][wk] = week_sal[emp].get(wk, 0) + sal
    return {"month": month, "year": year, "grand_total": grand, "by_worker": week_sal}


def _employee_name_from_init(x_init_data: str) -> str:
    uid = current_user_id(x_init_data)
    return load_users().get(str(uid), "")


def _my_day_stats(day_data: dict, name: str) -> dict:
    """Статистика одного мойщика за один день (сессия сегодня или день из архива)."""
    s = calculate_summary(day_data)
    my_cars = [c for c in day_data.get("cars", []) if c.get("employee") == name]
    return {
        "cars": len(my_cars),
        "salary": s["washer_salaries"].get(name, 0),
        "revenue": sum(c["price"] for c in my_cars),
        "car_list": [
            {
                "num": c.get("num"),
                "car": c.get("car") or "",
                "service": c.get("service") or "",
                "price": c.get("price", 0),
                "payment": c.get("payment", ""),
                "time": c.get("time", ""),
            }
            for c in my_cars
        ],
    }


@app.get("/api/my-employee-stats")
def api_my_employee_stats(branch: str, period: str = "today",
                           x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Личная статистика СВОЕГО заработка, объединяющая ВСЕ роли сотрудника
    (мойщик + администратор + любые будущие роли) — в отличие от /api/my-stats,
    которая видит только заработок мойщика. Доступ только к своим данным:
    имя берётся из привязки Telegram-аккаунта (load_users), не из параметров."""
    from employee_stats import get_branch_employee_roles, employee_period_stats, week_range, month_range
    name = _employee_name_from_init(x_init_data)
    roles = get_branch_employee_roles(branch)
    if not name or name not in roles:
        raise HTTPException(403, "Вы не привязаны как сотрудник этого филиала")

    today = datetime.now()
    if period == "today":
        date_from = today.replace(hour=0, minute=0, second=0, microsecond=0)
        date_to = today
    elif period == "week":
        date_from, date_to = week_range(today)
    elif period == "month":
        date_from, date_to = month_range(today.month, today.year)
    else:
        raise HTTPException(400, "period должен быть today|week|month")

    stats = employee_period_stats(branch, name, date_from, date_to)
    stats["roles"] = roles[name]
    stats["period"] = period
    stats["from"] = date_from.strftime("%d.%m.%Y")
    stats["to"] = date_to.strftime("%d.%m.%Y")
    return stats


@app.get("/api/my-stats")
def api_my_stats(branch: str, period: str = "today", x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Личная статистика мойщика: сегодня / неделя / месяц.
    Мойщик авторизуется своим Telegram-аккаунтом — привязка идёт через
    белый список пользователей (load_users: user_id → имя), сверенный со
    списком сотрудников филиала (get_branch_workers)."""
    name = _employee_name_from_init(x_init_data)
    if not name or name not in get_branch_workers(branch):
        raise HTTPException(403, "Вы не привязаны как сотрудник этого филиала")

    if period == "today":
        session = get_session(branch)
        stats = _my_day_stats(session, name)
        stats["date"] = session.get("date")
        return {"name": name, "period": "today", "stats": stats}

    if period not in ("week", "month"):
        raise HTTPException(400, "period должен быть today|week|month")

    today = datetime.now()
    if period == "week":
        start = (today - timedelta(days=today.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    archive = load_archive()
    branch_archive = archive.get(branch, {})

    days_out = []
    total_cars = total_salary = total_revenue = 0
    for date_str, day in branch_archive.items():
        try:
            dt = datetime.strptime(date_str, "%d.%m.%Y")
        except ValueError:
            continue
        if not (start <= dt <= today):
            continue
        st = _my_day_stats(day, name)
        if st["cars"] == 0:
            continue
        days_out.append({"date": date_str, **st})
        total_cars += st["cars"]; total_salary += st["salary"]; total_revenue += st["revenue"]

    session = get_session(branch)
    if session.get("cars"):
        st = _my_day_stats(session, name)
        if st["cars"] > 0:
            days_out.append({"date": session.get("date"), **st})
            total_cars += st["cars"]; total_salary += st["salary"]; total_revenue += st["revenue"]

    days_out.sort(key=lambda d: datetime.strptime(d["date"], "%d.%m.%Y"))
    return {
        "name": name, "period": period,
        "from": start.strftime("%d.%m.%Y"), "to": today.strftime("%d.%m.%Y"),
        "total_cars": total_cars, "total_salary": total_salary, "total_revenue": total_revenue,
        "days": days_out,
    }


@app.get("/api/worker-stats")
def api_worker_stats(branch: str, name: str, period: str = "today",
                      x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """То же самое, что /api/my-stats, но для АДМИНА — можно посмотреть
    историю зарплаты любого сотрудника филиала (день/неделя/месяц),
    а не только свою."""
    require_branch_admin(branch, x_init_data, x_site_token)
    if name not in get_branch_workers(branch):
        raise HTTPException(404, "Сотрудник не найден в этом филиале")

    if period == "today":
        session = get_session(branch)
        stats = _my_day_stats(session, name)
        stats["date"] = session.get("date")
        return {"name": name, "period": "today", "stats": stats}

    if period not in ("week", "month"):
        raise HTTPException(400, "period должен быть today|week|month")

    today = datetime.now()
    if period == "week":
        start = (today - timedelta(days=today.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    archive = load_archive()
    branch_archive = archive.get(branch, {})

    days_out = []
    total_cars = total_salary = total_revenue = 0
    for date_str, day in branch_archive.items():
        try:
            dt = datetime.strptime(date_str, "%d.%m.%Y")
        except ValueError:
            continue
        if not (start <= dt <= today):
            continue
        st = _my_day_stats(day, name)
        if st["cars"] == 0:
            continue
        days_out.append({"date": date_str, **st})
        total_cars += st["cars"]; total_salary += st["salary"]; total_revenue += st["revenue"]

    session = get_session(branch)
    if session.get("cars"):
        st = _my_day_stats(session, name)
        if st["cars"] > 0:
            days_out.append({"date": session.get("date"), **st})
            total_cars += st["cars"]; total_salary += st["salary"]; total_revenue += st["revenue"]

    days_out.sort(key=lambda d: datetime.strptime(d["date"], "%d.%m.%Y"))
    return {
        "name": name, "period": period,
        "from": start.strftime("%d.%m.%Y"), "to": today.strftime("%d.%m.%Y"),
        "total_cars": total_cars, "total_salary": total_salary, "total_revenue": total_revenue,
        "days": days_out,
    }


@app.get("/api/admin-stats")
def api_admin_stats(branch: str, name: str, period: str = "today",
                     x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Зарплата администратора (день/неделя/месяц) — по дням, где ИМЕННО этот
    человек был указан дежурным администратором (session["admin_name"]).
    Для дней ДО добавления этой функции admin_name в архиве пустой — такие
    дни в историю не попадут (посчитать задним числом, кто дежурил, нельзя)."""
    require_branch_admin(branch, x_init_data, x_site_token)
    if name not in get_branch_admin_names(branch):
        raise HTTPException(404, "Администратор не найден в этом филиале")

    def day_stats(session_dict):
        if session_dict.get("admin_name") != name:
            return None
        s = calculate_summary(session_dict)
        cars = session_dict.get("cars", [])
        if not cars and s["admin_salary"] == 0:
            return None
        return {"cars": len(cars), "revenue": s["total"], "salary": s["admin_salary"]}

    if period == "today":
        session = get_session(branch)
        st = day_stats(session) or {"cars": 0, "revenue": 0, "salary": 0}
        st["date"] = session.get("date")
        return {"name": name, "period": "today", "stats": st}

    if period not in ("week", "month"):
        raise HTTPException(400, "period должен быть today|week|month")

    today = datetime.now()
    if period == "week":
        start = (today - timedelta(days=today.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    archive = load_archive()
    branch_archive = archive.get(branch, {})

    days_out = []
    total_cars = total_salary = total_revenue = 0
    for date_str, day in branch_archive.items():
        try:
            dt = datetime.strptime(date_str, "%d.%m.%Y")
        except ValueError:
            continue
        if not (start <= dt <= today):
            continue
        st = day_stats(day)
        if not st:
            continue
        days_out.append({"date": date_str, **st})
        total_cars += st["cars"]; total_salary += st["salary"]; total_revenue += st["revenue"]

    session = get_session(branch)
    st = day_stats(session)
    if st:
        days_out.append({"date": session.get("date"), **st})
        total_cars += st["cars"]; total_salary += st["salary"]; total_revenue += st["revenue"]

    days_out.sort(key=lambda d: datetime.strptime(d["date"], "%d.%m.%Y"))
    return {
        "name": name, "period": period,
        "from": start.strftime("%d.%m.%Y"), "to": today.strftime("%d.%m.%Y"),
        "total_cars": total_cars, "total_salary": total_salary, "total_revenue": total_revenue,
        "days": days_out,
    }


@app.get("/api/employee-stats")
def api_employee_stats(branch: str, name: str, period: str = "today",
                        x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Единая карточка сотрудника: объединяет заработок по ВСЕМ его ролям
    (мойщик, администратор, и любые будущие роли) под одним именем — вместо
    того, чтобы показывать 'Иззет-мойщик' и 'Иззет-админ' как разных людей."""
    require_branch_admin(branch, x_init_data, x_site_token)
    from employee_stats import get_branch_employee_roles, employee_period_stats, week_range, month_range
    roles = get_branch_employee_roles(branch)
    if name not in roles:
        raise HTTPException(404, "Сотрудник не найден в этом филиале")

    today = datetime.now()
    if period == "today":
        date_from = today.replace(hour=0, minute=0, second=0, microsecond=0)
        date_to = today
    elif period == "week":
        date_from, date_to = week_range(today)
    elif period == "month":
        date_from, date_to = month_range(today.month, today.year)
    else:
        raise HTTPException(400, "period должен быть today|week|month")

    stats = employee_period_stats(branch, name, date_from, date_to)
    stats["roles"] = roles[name]
    stats["period"] = period
    stats["from"] = date_from.strftime("%d.%m.%Y")
    stats["to"] = date_to.strftime("%d.%m.%Y")
    return stats


@app.get("/api/employees-stats")
def api_employees_stats(branch: str, period: str = "today",
                         x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Сводная статистика по ВСЕМ сотрудникам филиала за период — каждый
    сотрудник встречается ровно один раз, с разбивкой заработка по ролям."""
    require_branch_admin(branch, x_init_data, x_site_token)
    from employee_stats import all_employees_period_stats, get_branch_employee_roles, week_range, month_range
    today = datetime.now()
    if period == "today":
        date_from = today.replace(hour=0, minute=0, second=0, microsecond=0)
        date_to = today
    elif period == "week":
        date_from, date_to = week_range(today)
    elif period == "month":
        date_from, date_to = month_range(today.month, today.year)
    else:
        raise HTTPException(400, "period должен быть today|week|month")

    roles_by_name = get_branch_employee_roles(branch)
    employees = all_employees_period_stats(branch, date_from, date_to)
    for emp in employees:
        emp["roles"] = roles_by_name.get(emp["name"], [])
    return {
        "period": period,
        "from": date_from.strftime("%d.%m.%Y"), "to": date_to.strftime("%d.%m.%Y"),
        "employees": employees,
        "grand_total": sum(e["total"] for e in employees),
    }


@app.get("/api/branches/summary")
def api_branches_summary(x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    """Сводка по всем филиалам (сегодня + тренд за 5 дней) — используется на
    экране выбора филиала. Доступна любому пользователю из белого списка бота
    (не любому человеку в интернете — initData подписан Telegram)."""
    require_access(x_init_data, x_site_token)
    archive = load_archive()
    today = datetime.now()
    out = []
    for branch in BRANCHES:
        session = get_session(branch)
        s = calculate_summary(session)
        branch_archive = archive.get(branch, {})
        trend = []
        for i in range(4, -1, -1):
            dt = today - timedelta(days=i)
            date_str = dt.strftime("%d.%m.%Y")
            if i == 0:
                trend.append(s["grand_total"])
            else:
                day = branch_archive.get(date_str)
                trend.append(calculate_summary(day)["grand_total"] if day else 0)
        out.append({
            "branch": branch,
            "total": s["grand_total"],
            "cars": len(session.get("cars", [])),
            "trend": trend,
        })
    return {"branches": out}


@app.get("/api/reports/allreport")
def api_report_allreport(x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_owner(x_init_data, x_site_token)
    branches_out = []
    grand = 0
    for branch in BRANCHES:
        session = get_session(branch)
        from sessions import session_has_data
        if not session_has_data(session):
            continue
        s = calculate_summary(session)
        grand += s["grand_total"]
        branches_out.append({
            "branch": branch, "cars": len(session["cars"]), "total": s["grand_total"],
            "cash": s["cash"], "visa": s["visa"], "beznal": s["beznal"],
        })
    return {"branches": branches_out, "grand_total": grand}


@app.get("/api/reports/pdf")
def api_report_pdf(branch: str, date: str = "", x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    archive = load_archive()
    if date:
        day_data = archive.get(branch, {}).get(date)
        if not day_data:
            raise HTTPException(404, f"Нет данных за {date} в «{branch}»")
    else:
        day_data = get_session(branch)
        date = day_data.get("date", datetime.now().strftime("%d.%m.%Y"))
        if not day_data.get("cars"):
            raise HTTPException(404, "Нет данных за сегодня")

    summary = calculate_summary(day_data)
    safe_branch = branch.replace(" ", "_")
    pdf_path = os.path.join(tempfile.gettempdir(), f"report_{safe_branch}_{date.replace('.', '')}.pdf")
    generate_pdf(day_data, summary, pdf_path)
    return FileResponse(pdf_path, media_type="application/pdf",
                        filename=f"Касса_{branch}_{date}.pdf")


@app.get("/api/reports/xlsx")
def api_report_xlsx(branch: str, date: str = "", x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    archive = load_archive()
    if date:
        day_data = archive.get(branch, {}).get(date)
        if not day_data:
            raise HTTPException(404, f"Нет данных за {date} в «{branch}»")
    else:
        day_data = get_session(branch)
        date = day_data.get("date", datetime.now().strftime("%d.%m.%Y"))
        if not day_data.get("cars"):
            raise HTTPException(404, "Нет данных за сегодня")

    summary = calculate_summary(day_data)
    safe_branch = branch.replace(" ", "_")
    xlsx_path = os.path.join(tempfile.gettempdir(), f"report_{safe_branch}_{date.replace('.', '')}.xlsx")
    generate_xlsx(day_data, summary, xlsx_path)
    return FileResponse(
        xlsx_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"Касса_{branch}_{date}.xlsx",
    )


# ── История изменений кассы ──────────────────────────────────────────────
@app.get("/api/history")
def api_history(branch: str, limit: int = 100, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_branch_admin(branch, x_init_data, x_site_token)
    return {"entries": get_history(branch, limit)}


# ── Пресеты услуг ────────────────────────────────────────────────────────
@app.get("/api/presets")
def api_get_presets(branch: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    return {"presets": list_presets(branch)}


@app.post("/api/presets")
def api_add_preset(body: PresetIn, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    if not body.service_keys and not body.custom_services:
        raise HTTPException(400, "Нужна хотя бы одна услуга в пресете")
    presets = add_preset(body.branch, body.name.strip(), body.service_keys, body.custom_services)
    return {"ok": True, "presets": presets}


@app.delete("/api/presets/{branch}/{name}")
def api_delete_preset(branch: str, name: str, x_init_data: str = Header(default=""), x_site_token: str = Header(default="")):
    require_access(x_init_data, x_site_token)
    presets = delete_preset(branch, name)
    return {"ok": True, "presets": presets}


# ── Статика (сама Mini App) ─────────────────────────────────────────────
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


# Обычный StaticFiles не запрещает кэш, а Telegram WebView (и мобильные
# браузеры) очень агрессивно кэшируют .html-страницы мини-аппы. Из-за этого
# после редеплоя на Railway пользователи продолжают видеть старый дизайн
# dashboard.html/cars.html/... пока не почистят кэш вручную.
# Регистрируем no-cache route для каждой HTML-страницы ДО mount'а ниже:
# Starlette проверяет маршруты в порядке регистрации и берёт первое
# совпадение, поэтому эти явные routes должны идти раньше "/static" mount,
# иначе mount перехватит запрос первым и до этих routes дело не дойдёт.
# CSS/JS/шрифты (остальное в /static/*) по-прежнему кэшируются браузером.
def _register_nocache_html_routes():
    for fname in os.listdir(STATIC_DIR):
        if not fname.endswith(".html"):
            continue
        full_path = os.path.join(STATIC_DIR, fname)

        def _make_handler(p=full_path):
            def _handler():
                return FileResponse(p, headers=NO_CACHE_HEADERS)
            return _handler

        app.get(f"/static/{fname}")(_make_handler())


_register_nocache_html_routes()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(
        os.path.join(STATIC_DIR, "index.html"),
        headers=NO_CACHE_HEADERS,
    )


# ── Отдельная ссылка на сайт (браузерная версия, вне Telegram) ───────────
# Мини-приложение живёт на "/", сайт — на "/site". Сама страница входа
# (site-login.html) уже умеет логиниться и дальше сама переключается между
# /static/dashboard.html, /static/cash.html и т.д. — это не трогаем.
@app.get("/site")
def site_entry():
    return FileResponse(
        os.path.join(STATIC_DIR, "site-login.html"),
        headers=NO_CACHE_HEADERS,
    )
