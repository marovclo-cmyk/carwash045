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
import sys, os, hashlib, hmac, json, tempfile
from datetime import datetime, timedelta
from typing import Optional, Dict
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
    load_archive, load_users, save_users, add_user, remove_user,
)
from calculator import calculate_summary
from pdf_generator import generate_pdf

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


def auth(x_init_data: str = Header(default="")) -> dict:
    # В деве можно временно закомментировать verify и просто распарсить user.
    return verify_init_data(x_init_data)


def auth_optional(x_init_data: str = Header(default="")) -> dict:
    try:
        return verify_init_data(x_init_data)
    except HTTPException:
        return {}


def current_user_id(x_init_data: str = Header(default="")) -> int:
    user = auth_optional(x_init_data)
    return int(user.get("id", 0))


def require_branch_admin(branch: str, x_init_data: str = Header(default="")):
    uid = current_user_id(x_init_data)
    if not is_branch_admin(uid, branch):
        raise HTTPException(403, "Нет прав администратора филиала")
    return uid


def require_owner(x_init_data: str = Header(default="")):
    uid = current_user_id(x_init_data)
    if uid != OWNER_ID:
        raise HTTPException(403, "Только для владельца")
    return uid


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
    comment: str = ""


class LoyaltyIn(BaseModel):
    branch: str
    car_num: int
    discount: int


class WorkerIn(BaseModel):
    branch: str
    name: str
    x_init_data: str = ""


class BranchAdminIn(BaseModel):
    branch: str
    user_id: int


class UserIn(BaseModel):
    user_id: int
    name: str = "Без имени"


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
def api_workers(branch: str):
    return {"workers": get_branch_workers(branch), "admin_id": get_branch_admin(branch)}


@app.get("/api/me")
def api_me(branch: str = "", x_init_data: str = Header(default="")):
    user = auth_optional(x_init_data)
    uid = int(user.get("id", 0))
    return {
        "user_id": uid,
        "name": user.get("first_name", ""),
        "is_owner": uid == OWNER_ID,
        "is_branch_admin": is_branch_admin(uid, branch) if branch else False,
    }


@app.post("/api/workers")
def api_add_worker(body: WorkerIn, x_init_data: str = Header(default="")):
    require_branch_admin(body.branch, x_init_data)
    added = add_branch_worker(body.branch, body.name.strip())
    if not added:
        raise HTTPException(400, "Такой сотрудник уже есть")
    return {"ok": True, "workers": get_branch_workers(body.branch)}


@app.delete("/api/workers/{branch}/{name}")
def api_remove_worker(branch: str, name: str, x_init_data: str = Header(default="")):
    require_branch_admin(branch, x_init_data)
    remove_branch_worker(branch, name)
    return {"ok": True, "workers": get_branch_workers(branch)}


@app.post("/api/branch-admin")
def api_set_branch_admin(body: BranchAdminIn, x_init_data: str = Header(default="")):
    uid = current_user_id(x_init_data)
    if uid != OWNER_ID and not is_branch_admin(uid, body.branch):
        raise HTTPException(403, "Нет прав")
    set_branch_admin(body.branch, body.user_id)
    return {"ok": True, "admin_id": get_branch_admin(body.branch)}


@app.get("/api/users")
def api_list_users(x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    users = load_users()
    return {"users": [{"user_id": int(uid), "name": name} for uid, name in users.items()]}


@app.post("/api/users")
def api_add_user(body: UserIn, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    add_user(body.user_id, body.name)
    return {"ok": True}


@app.delete("/api/users/{user_id}")
def api_remove_user(user_id: int, x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    remove_user(user_id)
    return {"ok": True}


# ── Смена ────────────────────────────────────────────────────────────────
@app.get("/api/session")
def api_session(branch: str):
    session = get_session(branch)
    summary = calculate_summary(session)
    return {"session": session, "summary": summary}


@app.post("/api/car")
def api_add_car(body: CarIn):
    session = get_session(body.branch)
    body_type = body.body_type

    breakdown = {}
    for k in body.service_keys:
        if k not in SERVICES:
            continue
        breakdown[k] = {
            "name": SERVICES[k]["name"],
            "price": get_service_price(k, body_type),
            "percent": SERVICES[k]["percent"],
        }
    for i, c in enumerate(body.custom_services):
        breakdown[f"custom_{i}"] = {
            "name": c["name"], "price": int(c["price"]), "percent": float(c["percent"]) / 100,
        }

    if not breakdown:
        raise HTTPException(400, "Нужна хотя бы одна услуга")

    total_price = sum(v["price"] for v in breakdown.values())
    if body.payment_split:
        split_sum = sum(body.payment_split.values())
        if split_sum != total_price:
            raise HTTPException(400, f"Сумма раздельной оплаты ({split_sum}₽) не совпадает со стоимостью ({total_price}₽)")

    num = len(session["cars"]) + 1
    car = {
        "num": num,
        "employee": body.employee,
        "body_type": body_type,
        "service_keys": body.service_keys,
        "custom_services": body.custom_services,
        "price_breakdown": breakdown,
        "service": " + ".join(v["name"] for v in breakdown.values()),
        "price": total_price,
        "car": body.car,
        "payment": body.payment,
        "payment_split": body.payment_split,
        "comment": body.comment,
        "time": datetime.now().strftime("%H:%M"),
    }
    session["cars"].append(car)
    save_sessions()
    return {"ok": True, "car": car, "summary": calculate_summary(session)}


@app.delete("/api/car/{branch}/{num}")
def api_delete_car(branch: str, num: int):
    session = get_session(branch)
    session["cars"] = [c for c in session["cars"] if c["num"] != num]
    save_sessions()
    return {"ok": True, "summary": calculate_summary(session)}


@app.post("/api/loyalty")
def api_add_loyalty(body: LoyaltyIn):
    session = get_session(body.branch)
    session.setdefault("loyalty", []).append({"car_num": body.car_num, "discount": body.discount})
    save_sessions()
    return {"ok": True, "summary": calculate_summary(session)}


# ── Отчёты ───────────────────────────────────────────────────────────────
@app.get("/api/archive")
def api_archive(branch: str, limit: int = 14):
    archive = load_archive()
    days = archive.get(branch, [])
    return {"days": days[-limit:][::-1]}


@app.post("/api/newday")
def api_newday(branch: str, x_init_data: str = Header(default="")):
    require_branch_admin(branch, x_init_data)
    session = get_session(branch)
    if session.get("cars") or session.get("products"):
        save_to_archive(branch, session)
    reset_session(branch)
    return {"ok": True}


@app.get("/api/reports/today")
def api_report_today(branch: str):
    session = get_session(branch)
    summary = calculate_summary(session)
    svc_count = {}
    for c in session["cars"]:
        svc_count[c.get("service", "—")] = svc_count.get(c.get("service", "—"), 0) + 1
    top = sorted(svc_count.items(), key=lambda x: x[1], reverse=True)[:5]
    return {"session": session, "summary": summary, "top_services": top}


@app.get("/api/reports/week")
def api_report_week(branch: str):
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
def api_report_month(branch: str, month: str, year: int = 0):
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


@app.get("/api/reports/allreport")
def api_report_allreport(x_init_data: str = Header(default="")):
    require_owner(x_init_data)
    branches_out = []
    grand = 0
    for branch in BRANCHES:
        session = get_session(branch)
        if not session.get("cars") and not session.get("products"):
            continue
        s = calculate_summary(session)
        grand += s["grand_total"]
        branches_out.append({
            "branch": branch, "cars": len(session["cars"]), "total": s["grand_total"],
            "cash": s["cash"], "visa": s["visa"], "beznal": s["beznal"],
        })
    return {"branches": branches_out, "grand_total": grand}


@app.get("/api/reports/pdf")
def api_report_pdf(branch: str, date: str = ""):
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


# ── Статика (сама Mini App) ─────────────────────────────────────────────
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
