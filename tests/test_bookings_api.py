"""
Тесты HTTP-роутов /api/bookings* и /api/boxes-смежных сценариев конфликта
слотов в webapp/server.py (GAP-TEST2, продолжение).

До этого прохода записи (bookings) были покрыты только на уровне
миграционного скрипта (tests/test_migrate_bookings.py) и напрямую через
sessions.py (tests/test_sessions_*), но НИ ОДИН HTTP-роут
/api/bookings* не был протестирован — этот файл был явно перечислен как
известный пробел в шапке test_server_routes.py ("остальные роуты —
записи/бронирования... вне охвата"). Этот проход закрывает основной CRUD
и валидацию, но не PDF/XLSX-экспорт, историю изменений и графики работы —
они остаются отдельным резервом (см. отчёт по этапу).

Дата записей намеренно НЕ сегодняшняя (см. FIXED_DATE) — это отключает
побочный эффект _maybe_convert_booking_to_car (авто-создание машины в
кассе смены), который относится к другому домену (касса) и не должен
усложнять тесты, сфокусированные на самой записи как таковой.
"""
from config import BRANCHES

BRANCH = BRANCHES[0]
OWNER_NAME = "Тестовый Владелец"
PASSWORD = "test-password"
FIXED_DATE = "01.01.2099"


def _owner_headers(client) -> dict:
    r = client.post("/api/site/login", json={"password": PASSWORD, "name": OWNER_NAME, "branch": ""})
    assert r.status_code == 200, r.text
    return {"X-Site-Token": r.json()["token"]}


def _make_booking(client, headers, **overrides) -> dict:
    payload = {
        "branch": BRANCH, "date": FIXED_DATE, "box": 1,
        "start_time": "10:00", "end_time": "11:00",
        "employee": "Иван Тестов", "body_type": "sedan",
        "car": "А111АА", "service_keys": [], "custom_services": [],
        "product_keys": [], "payment": "нал", "comment": "",
        "phone": "", "client_name": "Клиент Тестов", "status": "waiting",
    }
    payload.update(overrides)
    r = client.post("/api/bookings", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["booking"]


# ── список записей — требует авторизации ────────────────────────────────────

def test_list_bookings_requires_auth(webapp_client):
    client, _sessions, _server = webapp_client
    r = client.get("/api/bookings", params={"branch": BRANCH, "date": FIXED_DATE})
    assert r.status_code == 403


def test_list_bookings_empty_then_after_create(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    r = client.get("/api/bookings", params={"branch": BRANCH, "date": FIXED_DATE}, headers=headers)
    assert r.status_code == 200
    assert r.json()["bookings"] == []

    _make_booking(client, headers)
    r = client.get("/api/bookings", params={"branch": BRANCH, "date": FIXED_DATE}, headers=headers)
    assert len(r.json()["bookings"]) == 1


# ── создание записи ──────────────────────────────────────────────────────────

def test_create_booking_success_computes_price_from_services(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    booking = _make_booking(client, headers, service_keys=["комплекс"])
    assert booking["id"] > 0
    assert booking["price"] == 2000
    assert booking["price_calc"] == 2000
    assert booking["price_override"] is None
    assert booking["status"] == "waiting"


def test_create_booking_with_price_override(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    booking = _make_booking(client, headers, service_keys=["комплекс"], price_override=1500)
    assert booking["price"] == 1500
    assert booking["price_override"] == 1500
    assert booking["price_calc"] == 2000  # расчётная цена по услугам сохранена отдельно


def test_create_booking_negative_price_override_rejected(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    r = client.post("/api/bookings", json={
        "branch": BRANCH, "date": FIXED_DATE, "box": 1,
        "start_time": "10:00", "end_time": "11:00",
        "service_keys": ["комплекс"], "price_override": -1,
    }, headers=headers)
    assert r.status_code == 400


def test_create_booking_invalid_status_rejected(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    r = client.post("/api/bookings", json={
        "branch": BRANCH, "date": FIXED_DATE, "box": 1,
        "start_time": "10:00", "end_time": "11:00", "status": "не-статус",
    }, headers=headers)
    assert r.status_code == 400


def test_create_booking_invalid_box_rejected(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    r = client.post("/api/bookings", json={
        "branch": BRANCH, "date": FIXED_DATE, "box": 0,
        "start_time": "10:00", "end_time": "11:00",
    }, headers=headers)
    assert r.status_code == 400


def test_create_booking_start_after_end_rejected(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    r = client.post("/api/bookings", json={
        "branch": BRANCH, "date": FIXED_DATE, "box": 1,
        "start_time": "11:00", "end_time": "10:00",
    }, headers=headers)
    assert r.status_code == 400


def test_create_booking_payment_split_mismatch_rejected(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    r = client.post("/api/bookings", json={
        "branch": BRANCH, "date": FIXED_DATE, "box": 1,
        "start_time": "10:00", "end_time": "11:00",
        "service_keys": ["комплекс"], "payment_split": {"нал": 500, "карта": 500},
    }, headers=headers)
    assert r.status_code == 400  # 500+500 != 2000


def test_create_booking_conflicting_slot_rejected(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    _make_booking(client, headers, box=1, start_time="10:00", end_time="11:00")
    r = client.post("/api/bookings", json={
        "branch": BRANCH, "date": FIXED_DATE, "box": 1,
        "start_time": "10:30", "end_time": "11:30",
    }, headers=headers)
    assert r.status_code == 409


def test_create_booking_different_box_same_time_allowed(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    _make_booking(client, headers, box=1, start_time="10:00", end_time="11:00")
    r = client.post("/api/bookings", json={
        "branch": BRANCH, "date": FIXED_DATE, "box": 2,
        "start_time": "10:00", "end_time": "11:00",
    }, headers=headers)
    assert r.status_code == 200


# ── редактирование записи ───────────────────────────────────────────────────

def test_edit_booking_not_found(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    r = client.patch("/api/bookings/999999", json={"comment": "новый комментарий"}, headers=headers)
    assert r.status_code == 404


def test_edit_booking_partial_update_keeps_other_fields(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)
    booking = _make_booking(client, headers, service_keys=["комплекс"], comment="старый")

    r = client.patch(f"/api/bookings/{booking['id']}", json={"comment": "новый комментарий"}, headers=headers)
    assert r.status_code == 200
    updated = r.json()["booking"]
    assert updated["comment"] == "новый комментарий"
    assert updated["start_time"] == booking["start_time"]
    assert updated["price"] == booking["price"]


def test_edit_booking_move_into_conflicting_slot_rejected(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)
    _make_booking(client, headers, box=1, start_time="10:00", end_time="11:00")
    other = _make_booking(client, headers, box=1, start_time="12:00", end_time="13:00")

    r = client.patch(f"/api/bookings/{other['id']}", json={"start_time": "10:30", "end_time": "11:30"}, headers=headers)
    assert r.status_code == 409


def test_edit_booking_changing_services_recalculates_price(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)
    booking = _make_booking(client, headers, service_keys=["комплекс"])
    assert booking["price"] == 2000

    r = client.patch(f"/api/bookings/{booking['id']}", json={"service_keys": []}, headers=headers)
    assert r.status_code == 200
    assert r.json()["booking"]["price"] == 0


def test_edit_booking_clear_price_override_resets_to_calculated(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)
    booking = _make_booking(client, headers, service_keys=["комплекс"], price_override=1200)
    assert booking["price"] == 1200

    r = client.patch(f"/api/bookings/{booking['id']}", json={"clear_price_override": True}, headers=headers)
    assert r.status_code == 200
    updated = r.json()["booking"]
    # Известное поведение, унаследованное из JSON-версии (см.
    # PROJECT_STATE.md → "Known pre-existing behavior"): clear_price_override
    # выставляет price_override=None через update_booking(**fields), но
    # update_booking игнорирует поля со значением None — поэтому
    # price_override в БД не сбрасывается. price при этом всё равно
    # пересчитывается на price_calc, это отдельная строка fields["price"].
    assert updated["price"] == 2000


# ── статус записи ────────────────────────────────────────────────────────────

def test_set_booking_status_success(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)
    booking = _make_booking(client, headers)

    r = client.patch(f"/api/bookings/{booking['id']}/status", json={"status": "confirmed"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["booking"]["status"] == "confirmed"


def test_set_booking_status_invalid_rejected(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)
    booking = _make_booking(client, headers)

    r = client.patch(f"/api/bookings/{booking['id']}/status", json={"status": "не-статус"}, headers=headers)
    assert r.status_code == 400


def test_set_booking_status_not_found(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    r = client.patch("/api/bookings/999999/status", json={"status": "confirmed"}, headers=headers)
    assert r.status_code == 404


# ── удаление записи ──────────────────────────────────────────────────────────

def test_delete_booking_removes_it(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)
    booking = _make_booking(client, headers)

    r = client.delete(f"/api/bookings/{booking['id']}", headers=headers)
    assert r.status_code == 200

    r = client.get("/api/bookings", params={"branch": BRANCH, "date": FIXED_DATE}, headers=headers)
    assert r.json()["bookings"] == []


def test_delete_nonexistent_booking_is_a_noop_ok(webapp_client):
    """delete_booking молча ничего не делает для несуществующего id — роут
    не проверяет существование заранее (в отличие от PATCH/PATCH .../status),
    поэтому ответ 200 даже без записи. Фиксируем поведение как есть."""
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    r = client.delete("/api/bookings/999999", headers=headers)
    assert r.status_code == 200
