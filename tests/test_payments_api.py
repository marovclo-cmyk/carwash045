"""
Тесты HTTP-роутов оплаты (GAP-PAY1): POST/GET /api/payments,
POST /api/payments/{id}/mock-confirm, GET /pay/{id}.

Изоляция — через фикстуру webapp_client (см. conftest.py). Провайдер в
тестах всегда мок (никакие YOOKASSA_*-переменные не заданы), поэтому
mock-confirm доступен без дополнительной настройки.
"""
from config import BRANCHES

BRANCH = BRANCHES[0]
OWNER_NAME = "Тестовый Владелец"
PASSWORD = "test-password"


def _owner_headers(client) -> dict:
    r = client.post("/api/site/login", json={"password": PASSWORD, "name": OWNER_NAME, "branch": ""})
    assert r.status_code == 200, r.text
    return {"X-Site-Token": r.json()["token"]}


def _open_day_with_car(client, headers, sessions_module, price=1000) -> int:
    """Открывает смену и добавляет машину, возвращает её num."""
    client.post("/api/openday", params={"branch": BRANCH}, headers=headers)
    r = client.post("/api/car", json={
        "branch": BRANCH, "employee": "Иван", "body_type": "sedan",
        "service_keys": [], "custom_services": [{"name": "Тест", "price": price, "percent": 0}],
        "payment": "нал", "car": "А000АА",
    }, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["car"]["num"]


def _create_booking(sessions_module, price=1000) -> int:
    booking = sessions_module.create_booking(
        BRANCH, "2026-08-15", 1, "10:00", "10:30", price=price, client_name="Клиент",
    )
    return booking["id"]


# ── POST /api/payments — валидация ──────────────────────────────────────────

def test_create_payment_requires_auth(webapp_client):
    client, _sessions, _server = webapp_client
    r = client.post("/api/payments", json={"branch": BRANCH, "purpose": "advance", "amount": 100})
    assert r.status_code == 403


def test_create_payment_rejects_bad_purpose(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)
    r = client.post("/api/payments", json={"branch": BRANCH, "purpose": "lunch", "amount": 100}, headers=headers)
    assert r.status_code == 400


def test_create_payment_rejects_non_positive_amount(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)
    r = client.post("/api/payments", json={"branch": BRANCH, "purpose": "advance", "amount": 0}, headers=headers)
    assert r.status_code == 400


def test_create_payment_advance_requires_existing_booking(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)
    r = client.post("/api/payments", json={
        "branch": BRANCH, "purpose": "advance", "amount": 500, "booking_id": 999999,
    }, headers=headers)
    assert r.status_code == 404


def test_create_payment_car_requires_existing_car_in_kassa(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)
    r = client.post("/api/payments", json={
        "branch": BRANCH, "purpose": "car", "amount": 500, "car_num": 999999,
    }, headers=headers)
    assert r.status_code == 404


# ── happy path ───────────────────────────────────────────────────────────────

def test_create_payment_advance_returns_pending_link(webapp_client):
    client, sessions_module, _server = webapp_client
    headers = _owner_headers(client)
    booking_id = _create_booking(sessions_module)

    r = client.post("/api/payments", json={
        "branch": BRANCH, "purpose": "advance", "amount": 500, "booking_id": booking_id,
    }, headers=headers)

    assert r.status_code == 200, r.text
    payment = r.json()["payment"]
    assert payment["status"] == "pending"
    assert payment["confirmation_url"]

    got = client.get(f"/api/payments/{payment['id']}", headers=headers)
    assert got.status_code == 200
    assert got.json()["id"] == payment["id"]


def test_mock_confirm_marks_paid_and_updates_booking(webapp_client):
    client, sessions_module, _server = webapp_client
    headers = _owner_headers(client)
    booking_id = _create_booking(sessions_module)
    payment = client.post("/api/payments", json={
        "branch": BRANCH, "purpose": "advance", "amount": 500, "booking_id": booking_id,
    }, headers=headers).json()["payment"]

    r = client.post(f"/api/payments/{payment['id']}/mock-confirm")
    assert r.status_code == 200, r.text
    assert r.json()["payment"]["status"] == "succeeded"

    booking = sessions_module.get_booking(booking_id)
    assert booking["prepayment"]["status"] == "paid"
    assert booking["prepayment"]["amount"] == 500


def test_mock_confirm_missing_payment_404(webapp_client):
    client, _sessions, _server = webapp_client
    r = client.post("/api/payments/does-not-exist/mock-confirm")
    assert r.status_code == 404


def test_mock_confirm_blocked_when_real_provider_active(webapp_client):
    """Dev-эндпоинт mock-confirm обязан быть недоступен, если подключён
    боевой провайдер (см. payment_provider.is_mock_active()). Реального
    переключения на YooKassaProvider здесь не поднимаем (нет сети/ключей
    в тестовом окружении) — подменяем только флаг, который читает роут."""
    client, _sessions, server = webapp_client
    headers = _owner_headers(client)
    booking_id = _create_booking(_sessions)
    payment = client.post("/api/payments", json={
        "branch": BRANCH, "purpose": "advance", "amount": 500, "booking_id": booking_id,
    }, headers=headers).json()["payment"]

    original = server.is_mock_active
    server.is_mock_active = lambda: False
    try:
        r = client.post(f"/api/payments/{payment['id']}/mock-confirm")
        assert r.status_code == 403
    finally:
        server.is_mock_active = original


def test_mock_pay_page_renders_amount(webapp_client):
    client, sessions_module, _server = webapp_client
    headers = _owner_headers(client)
    booking_id = _create_booking(sessions_module)
    payment = client.post("/api/payments", json={
        "branch": BRANCH, "purpose": "advance", "amount": 777, "booking_id": booking_id,
    }, headers=headers).json()["payment"]

    r = client.get(f"/pay/{payment['id']}")
    assert r.status_code == 200
    assert "777" in r.text


def test_car_payment_confirm_sets_payment_split(webapp_client):
    client, sessions_module, _server = webapp_client
    headers = _owner_headers(client)
    car_num = _open_day_with_car(client, headers, sessions_module, price=1000)

    payment = client.post("/api/payments", json={
        "branch": BRANCH, "purpose": "car", "amount": 1000, "car_num": car_num,
    }, headers=headers).json()["payment"]
    client.post(f"/api/payments/{payment['id']}/mock-confirm")

    session = sessions_module.get_session(BRANCH)
    car = next(c for c in session["cars"] if c["num"] == car_num)
    assert car["payment_split"] == {"онлайн": 1000}


# ── POST /api/payments/webhook/yookassa (GAP-PAY1, часть 3 — верификация) ──
#
# В мок-режиме (is_mock_active() True, как во всех этих тестах — YOOKASSA_*
# не заданы) проверки IP/провайдера отключены (см. Changes в CHANGELOG,
# "часть 3") — вебхук в проде не используется в этом режиме, статус берётся
# прямо из тела запроса. Боевой режим (is_mock_active() False) подменяется
# так же, как в test_mock_confirm_blocked_when_real_provider_active — через
# monkeypatch функции, которую реально читает роут, без реального подключения
# YooKassaProvider (нет сети/ключей в тестовом окружении).

def _webhook_body(payment_id: str, event: str, status: str) -> dict:
    return {"type": "notification", "event": event, "object": {"id": payment_id, "status": status}}


def test_webhook_mock_mode_trusts_body_status(webapp_client):
    client, sessions_module, _server = webapp_client
    record = sessions_module.create_payment(BRANCH, "advance", 300)

    r = client.post("/api/payments/webhook/yookassa",
                     json=_webhook_body(record["id"], "payment.succeeded", "succeeded"))

    assert r.status_code == 200, r.text
    assert sessions_module.get_payment(record["id"])["status"] == "succeeded"


def test_webhook_real_mode_rejects_unknown_ip(webapp_client):
    client, _sessions, server = webapp_client
    original = server.is_mock_active
    server.is_mock_active = lambda: False
    try:
        r = client.post("/api/payments/webhook/yookassa",
                         json=_webhook_body("any-id", "payment.succeeded", "succeeded"),
                         headers={"X-Forwarded-For": "8.8.8.8"})
        assert r.status_code == 403
    finally:
        server.is_mock_active = original


def test_webhook_real_mode_accepts_official_ip_and_reverifies_via_provider(webapp_client):
    """Даже с разрешённым IP тело вебхука не доверяется напрямую — статус
    переспрашивается через get_provider().get_payment(). Здесь подменяется
    сам get_provider (как реальный HTTP-вызов к ЮKassa недоступен в тестах),
    чтобы проверить, что роут действительно его вызывает и полагается на
    его ответ, а не на body."""
    client, sessions_module, server = webapp_client
    booking = sessions_module.create_booking(BRANCH, "2026-08-15", 1, "10:00", "10:30", price=1000)
    record = sessions_module.create_payment(BRANCH, "advance", 400, booking_id=booking["id"])

    class FakeProvider:
        def get_payment(self, payment_id):
            return {"id": payment_id, "status": "succeeded"}

    original_mock = server.is_mock_active
    original_provider = server.get_provider
    server.is_mock_active = lambda: False
    server.get_provider = lambda: FakeProvider()
    try:
        r = client.post("/api/payments/webhook/yookassa",
                         # тело намеренно врёт про статус ("pending") — должно
                         # игнорироваться, побеждает ответ FakeProvider
                         json=_webhook_body(record["id"], "payment.succeeded", "pending"),
                         headers={"X-Forwarded-For": "77.75.153.10"})
        assert r.status_code == 200, r.text
    finally:
        server.is_mock_active = original_mock
        server.get_provider = original_provider

    assert sessions_module.get_payment(record["id"])["status"] == "succeeded"


def test_webhook_real_mode_ignores_forged_body_status_without_matching_provider(webapp_client):
    """Тело утверждает succeeded, но реальный провайдер говорит pending —
    платёж НЕ должен примениться (защита от поддельного вебхука с верным
    IP, но без реального события на стороне ЮKassa)."""
    client, sessions_module, server = webapp_client
    record = sessions_module.create_payment(BRANCH, "advance", 250)

    class FakeProviderStillPending:
        def get_payment(self, payment_id):
            return {"id": payment_id, "status": "pending"}

    original_mock = server.is_mock_active
    original_provider = server.get_provider
    server.is_mock_active = lambda: False
    server.get_provider = lambda: FakeProviderStillPending()
    try:
        r = client.post("/api/payments/webhook/yookassa",
                         json=_webhook_body(record["id"], "payment.succeeded", "succeeded"),
                         headers={"X-Forwarded-For": "77.75.153.10"})
        assert r.status_code == 200, r.text
    finally:
        server.is_mock_active = original_mock
        server.get_provider = original_provider

    assert sessions_module.get_payment(record["id"])["status"] == "pending"
