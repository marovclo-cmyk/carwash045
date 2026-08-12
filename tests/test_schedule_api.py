"""
Тесты HTTP-роутов графика работы сотрудников — `/api/schedule`,
`/api/schedule/{branch}/{name}`, `/api/schedule/week` — в webapp/server.py
(GAP-TEST2, продолжение).

Ранее ни один из этих трёх роутов не был покрыт тестами — они прямо
названы как один из известных пробелов (см. шапку test_server_routes.py
и Next в tests/test_bookings_api.py). Домен хранения — `branches`
(GAP-DB1, этап 3: `set_worker_schedule`/`clear_worker_schedule` пишут в
`BranchConfigModel.schedules`, JSON-колонка) — уже покрыт на уровне
sessions.py косвенно через миграционные тесты, но не через HTTP.
"""
from datetime import date, timedelta

from config import BRANCHES

BRANCH = BRANCHES[0]
OWNER_NAME = "Тестовый Владелец"
PASSWORD = "test-password"
WORKER = "Иван Тестов"


def _owner_headers(client) -> dict:
    r = client.post("/api/site/login", json={"password": PASSWORD, "name": OWNER_NAME, "branch": ""})
    assert r.status_code == 200, r.text
    return {"X-Site-Token": r.json()["token"]}


def _worker_headers(client) -> dict:
    r = client.post("/api/site/login", json={"password": PASSWORD, "name": WORKER, "branch": BRANCH})
    assert r.status_code == 200, r.text
    return {"X-Site-Token": r.json()["token"]}


# ── POST /api/schedule — требует прав администратора филиала ───────────────

def test_set_schedule_requires_branch_admin_not_just_access(webapp_client):
    client, sessions_mod, _server = webapp_client
    sessions_mod.add_branch_worker(BRANCH, WORKER)
    headers = _worker_headers(client)

    r = client.post("/api/schedule", json={
        "branch": BRANCH, "name": WORKER, "work_days": 2, "rest_days": 2, "start_date": "2026-08-10",
    }, headers=headers)
    assert r.status_code == 403


def test_set_schedule_unknown_worker_rejected(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    r = client.post("/api/schedule", json={
        "branch": BRANCH, "name": "Нет Такого", "work_days": 2, "rest_days": 2, "start_date": "2026-08-10",
    }, headers=headers)
    assert r.status_code == 404


def test_set_schedule_invalid_days_rejected(webapp_client):
    client, sessions_mod, _server = webapp_client
    sessions_mod.add_branch_worker(BRANCH, WORKER)
    headers = _owner_headers(client)

    r = client.post("/api/schedule", json={
        "branch": BRANCH, "name": WORKER, "work_days": 0, "rest_days": 2, "start_date": "2026-08-10",
    }, headers=headers)
    assert r.status_code == 400

    r = client.post("/api/schedule", json={
        "branch": BRANCH, "name": WORKER, "work_days": 2, "rest_days": -1, "start_date": "2026-08-10",
    }, headers=headers)
    assert r.status_code == 400


def test_set_schedule_success_reflected_in_status(webapp_client):
    client, sessions_mod, _server = webapp_client
    sessions_mod.add_branch_worker(BRANCH, WORKER)
    headers = _owner_headers(client)

    today = date.today().isoformat()
    r = client.post("/api/schedule", json={
        "branch": BRANCH, "name": WORKER, "work_days": 2, "rest_days": 2, "start_date": today,
    }, headers=headers)
    assert r.status_code == 200, r.text
    status = r.json()["schedule"]
    assert status[WORKER]["working"] is True  # день 0 цикла — в пределах work_days
    assert status[WORKER]["schedule"] == {"work": 2, "rest": 2, "start": today}


# ── DELETE /api/schedule/{branch}/{name} ────────────────────────────────────

def test_clear_schedule_requires_branch_admin(webapp_client):
    client, sessions_mod, _server = webapp_client
    sessions_mod.add_branch_worker(BRANCH, WORKER)
    headers = _worker_headers(client)

    r = client.delete(f"/api/schedule/{BRANCH}/{WORKER}", headers=headers)
    assert r.status_code == 403


def test_clear_schedule_removes_it(webapp_client):
    client, sessions_mod, _server = webapp_client
    sessions_mod.add_branch_worker(BRANCH, WORKER)
    headers = _owner_headers(client)
    client.post("/api/schedule", json={
        "branch": BRANCH, "name": WORKER, "work_days": 2, "rest_days": 2, "start_date": "2026-08-10",
    }, headers=headers)

    r = client.delete(f"/api/schedule/{BRANCH}/{WORKER}", headers=headers)
    assert r.status_code == 200
    assert r.json()["schedule"][WORKER]["schedule"] is None
    # Без графика is_working_on считает мойщика доступным всегда (True) — см. sessions.py.
    assert r.json()["schedule"][WORKER]["working"] is True


def test_clear_schedule_on_worker_without_one_is_a_noop_ok(webapp_client):
    """clear_worker_schedule возвращает False, если графика не было, но
    роут не проверяет этот результат — отвечает 200 в любом случае.
    Фиксируем поведение как есть."""
    client, sessions_mod, _server = webapp_client
    sessions_mod.add_branch_worker(BRANCH, WORKER)
    headers = _owner_headers(client)

    r = client.delete(f"/api/schedule/{BRANCH}/{WORKER}", headers=headers)
    assert r.status_code == 200


# ── GET /api/schedule/week ───────────────────────────────────────────────────

def test_schedule_week_requires_auth(webapp_client):
    client, _sessions, _server = webapp_client
    r = client.get("/api/schedule/week", params={"branch": BRANCH})
    assert r.status_code == 403


def test_schedule_week_defaults_to_current_week_monday(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    r = client.get("/api/schedule/week", params={"branch": BRANCH}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    today = date.today()
    expected_monday = today - timedelta(days=today.weekday())
    assert body["monday"] == expected_monday.isoformat()
    assert len(body["day_labels"]) == 7
    assert body["weekday_labels"] == ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def test_schedule_week_explicit_monday_and_worker_rows(webapp_client):
    client, sessions_mod, _server = webapp_client
    sessions_mod.add_branch_worker(BRANCH, WORKER)
    headers = _owner_headers(client)
    client.post("/api/schedule", json={
        "branch": BRANCH, "name": WORKER, "work_days": 5, "rest_days": 2, "start_date": "2026-08-10",
    }, headers=headers)

    r = client.get("/api/schedule/week", params={"branch": BRANCH, "monday": "2026-08-10"}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["monday"] == "2026-08-10"
    assert WORKER in body["workers"]
    row = body["workers"][WORKER]
    assert len(row) == 7
    # start_date == monday, work=5/rest=2 — Пн..Пт рабочие, Сб..Вс — нет.
    assert row == [True, True, True, True, True, False, False]


def test_schedule_week_invalid_monday_rejected(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    r = client.get("/api/schedule/week", params={"branch": BRANCH, "monday": "не-дата"}, headers=headers)
    assert r.status_code == 400
