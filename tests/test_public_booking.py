# -*- coding: utf-8 -*-
"""GAP-CLIENT-PORTAL, этап 1: публичная витрина записи без верификации
телефона. Проверяем сквозной сценарий через HTTP (webapp_client) — эндпоинты
не требуют авторизации, поэтому тестируем именно их, а не sessions.py напрямую."""
import pytest


BRANCH = "Карла Маркса"


def _open_branch_with_box(sessions_module):
    box = sessions_module.add_branch_box(BRANCH, "Бокс 1")
    return box["id"]


def test_public_slots_empty_without_boxes(webapp_client):
    client, sessions_module, server_module = webapp_client
    r = client.get("/api/public/slots", params={"branch": BRANCH, "date": "01.09.2026"})
    assert r.status_code == 200
    assert r.json()["slots"] == []


def test_public_slots_and_create_and_conflict(webapp_client):
    client, sessions_module, server_module = webapp_client
    _open_branch_with_box(sessions_module)
    date = "01.09.2026"

    r = client.get("/api/public/slots", params={"branch": BRANCH, "date": date})
    assert r.status_code == 200
    slots = r.json()["slots"]
    assert "08:00" in slots
    assert r.json()["duration_min"] == 60

    payload = {
        "branch": BRANCH, "date": date, "start_time": "08:00",
        "body_type": "sedan", "service_keys": ["комплекс"],
        "phone": "+7 999 111-22-33", "client_name": "Иван",
    }
    r = client.post("/api/public/booking", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    booking = body["booking"]
    assert booking["status"] == "waiting"
    assert booking["end_time"] == "09:00"
    assert booking["price"] == 2000  # комплекс/седан из config.SERVICES

    # тот же слот с единственным боксом теперь занят
    r2 = client.get("/api/public/slots", params={"branch": BRANCH, "date": date})
    assert "08:00" not in r2.json()["slots"]

    # повторная попытка забронировать то же время — конфликт
    r3 = client.post("/api/public/booking", json=payload)
    assert r3.status_code == 409


def test_public_booking_unknown_branch_rejected(webapp_client):
    client, sessions_module, server_module = webapp_client
    r = client.get("/api/public/slots", params={"branch": "Несуществующий", "date": "01.09.2026"})
    assert r.status_code == 404


def test_public_booking_requires_name_and_valid_phone(webapp_client):
    client, sessions_module, server_module = webapp_client
    _open_branch_with_box(sessions_module)
    base = {
        "branch": BRANCH, "date": "01.09.2026", "start_time": "09:00",
        "body_type": "sedan", "service_keys": [],
    }
    r = client.post("/api/public/booking", json={**base, "phone": "123", "client_name": "Иван"})
    assert r.status_code == 400
    r = client.post("/api/public/booking", json={**base, "phone": "+79991112233", "client_name": "  "})
    assert r.status_code == 400


def test_public_list_reschedule_cancel_by_phone_no_verification(webapp_client):
    client, sessions_module, server_module = webapp_client
    _open_branch_with_box(sessions_module)
    date = "01.09.2026"
    phone = "+7 999 111-22-33"
    r = client.post("/api/public/booking", json={
        "branch": BRANCH, "date": date, "start_time": "08:00",
        "body_type": "sedan", "service_keys": [], "phone": phone, "client_name": "Иван",
    })
    booking_id = r.json()["booking"]["id"]

    # список по телефону — без пароля, только сам номер
    r = client.get("/api/public/bookings", params={"phone": phone})
    assert r.status_code == 200
    assert len(r.json()["bookings"]) == 1

    # чужой номер не видит запись
    r = client.get("/api/public/bookings", params={"phone": "+79990000000"})
    assert r.json()["bookings"] == []

    # перенос — тот же номер, без верификации
    r = client.patch(f"/api/public/booking/{booking_id}", json={
        "phone": phone, "date": date, "start_time": "10:00",
    })
    assert r.status_code == 200
    assert r.json()["booking"]["start_time"] == "10:00"

    # чужой номер не может ни перенести, ни отменить
    r = client.patch(f"/api/public/booking/{booking_id}", json={
        "phone": "+79990000000", "date": date, "start_time": "11:00",
    })
    assert r.status_code == 404
    r = client.delete(f"/api/public/booking/{booking_id}", params={"phone": "+79990000000"})
    assert r.status_code == 404

    # отмена своим номером — слот освобождается (статус no_show)
    r = client.delete(f"/api/public/booking/{booking_id}", params={"phone": phone})
    assert r.status_code == 200
    r = client.get("/api/public/bookings", params={"phone": phone})
    assert r.json()["bookings"] == []  # done/no_show скрыты из активного списка витрины
