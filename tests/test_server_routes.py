"""
Тесты HTTP-роутов webapp/server.py (Mini App / сайт backend).

До этого прохода ни один HTTP-роут не был покрыт тестами (GAP-TEST1 явно
исключал webapp/server.py и handlers/* из охвата — см. "Известная
граница" в PROJECT_STATE.md). Это ПЕРВЫЙ проход: он покрывает
авторизацию (вход по сайтовому паролю + отказ без токена/初initData),
основной happy-path кассы (открытие смены → добавление машины →
пересчёт кассы) и пару репрезентативных CRUD-ручек (боксы, скидка
клиента) — не все 80+ роутов файла. Остальные роуты (записи/бронирования,
PDF/XLSX-экспорт, история изменений, графики работы и т.д.) — сознательно
вне охвата этого прохода, чтобы не растягивать этап; см. отчёт по этапу.

Изоляция: фикстура `webapp_client` (см. conftest.py) поднимает
FastAPI TestClient с DATA_DIR/SITE_PASSWORD/SITE_OWNER_NAMES/BOT_TOKEN,
подменёнными на тестовые значения — реальное хранилище и реальный бот не
затрагиваются.
"""
from config import BRANCHES

BRANCH = BRANCHES[0]
OWNER_NAME = "Тестовый Владелец"
PASSWORD = "test-password"


def _login_owner(client) -> dict:
    r = client.post("/api/site/login", json={"password": PASSWORD, "name": OWNER_NAME, "branch": ""})
    assert r.status_code == 200, r.text
    return r.json()


def _owner_headers(client) -> dict:
    token = _login_owner(client)["token"]
    return {"X-Site-Token": token}


# ── /api/config — публичный справочник, без авторизации ────────────────────

def test_api_config_is_public_and_lists_branches(webapp_client):
    client, _sessions, _server = webapp_client
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["branches"] == BRANCHES


# ── вход по сайтовому паролю (GAP-S1) ───────────────────────────────────────

def test_site_login_wrong_password_rejected(webapp_client):
    client, _sessions, _server = webapp_client
    r = client.post("/api/site/login", json={"password": "wrongpass", "name": OWNER_NAME, "branch": ""})
    assert r.status_code == 401


def test_site_login_non_ascii_wrong_password_returns_401_not_500(webapp_client):
    """Регресс-тест на найденный в этом проходе баг: secrets.compare_digest()
    бросает TypeError (→ 500) на не-ASCII вводе вместо штатного 401.
    Опечатка на кириллической раскладке — реалистичный сценарий именно
    для этого продукта. Исправлено в auth_web.login() сравнением байтов."""
    client, _sessions, _server = webapp_client
    r = client.post("/api/site/login", json={"password": "неверный", "name": OWNER_NAME, "branch": ""})
    assert r.status_code == 401


def test_site_login_name_not_in_owner_list_rejected(webapp_client):
    client, _sessions, _server = webapp_client
    r = client.post("/api/site/login", json={"password": PASSWORD, "name": "Случайный Человек", "branch": ""})
    assert r.status_code == 403


def test_site_login_success_returns_role_by_system_not_by_input(webapp_client):
    """Роль на сайте определяется системой (списками сотрудников/владельцев),
    а не тем, что человек выбрал в форме — см. auth_web.py (GAP-S1)."""
    client, _sessions, _server = webapp_client
    data = _login_owner(client)
    assert data["role"] == "владелец"
    assert data["name"] == OWNER_NAME


def test_site_login_worker_role_resolved_from_branch_roster(webapp_client):
    client, sessions_mod, _server = webapp_client
    sessions_mod.add_branch_worker(BRANCH, "Иван Мойщиков")
    r = client.post("/api/site/login", json={"password": PASSWORD, "name": "Иван Мойщиков", "branch": BRANCH})
    assert r.status_code == 200
    assert r.json()["role"] == "мойщик"


def test_site_me_requires_valid_token(webapp_client):
    client, _sessions, _server = webapp_client
    r = client.get("/api/site/me")
    assert r.status_code == 401


def test_site_logout_invalidates_token(webapp_client):
    client, _sessions, _server = webapp_client
    token = _login_owner(client)["token"]
    headers = {"X-Site-Token": token}
    assert client.get("/api/site/me", headers=headers).status_code == 200

    r = client.post("/api/site/logout", headers=headers)
    assert r.status_code == 200

    assert client.get("/api/site/me", headers=headers).status_code == 401


# ── require_access: доступ к кассе без токена/initData закрыт ──────────────

def test_session_endpoint_requires_auth(webapp_client):
    client, _sessions, _server = webapp_client
    r = client.get("/api/session", params={"branch": BRANCH})
    assert r.status_code == 403


def test_session_endpoint_rejects_bogus_site_token(webapp_client):
    client, _sessions, _server = webapp_client
    r = client.get("/api/session", params={"branch": BRANCH}, headers={"X-Site-Token": "not-a-real-token-123"})
    assert r.status_code == 403


# ── happy path: открыть смену → добавить машину → касса пересчиталась ──────

def test_add_worker_open_day_add_car_updates_session_summary(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    r = client.post("/api/workers", json={"branch": BRANCH, "name": "Иван Тестов"}, headers=headers)
    assert r.status_code == 200
    assert "Иван Тестов" in r.json()["workers"]

    r = client.post("/api/openday", params={"branch": BRANCH}, headers=headers)
    assert r.status_code == 200

    r = client.post("/api/car", json={
        "branch": BRANCH, "employee": "Иван Тестов", "body_type": "sedan",
        "service_keys": ["комплекс"], "payment": "нал",
    }, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["car"]["price"] == 2000

    r = client.get("/api/session", params={"branch": BRANCH}, headers=headers)
    body = r.json()
    assert len(body["session"]["cars"]) == 1
    assert body["summary"]["total"] == 2000
    assert body["summary"]["cash"] == 2000


def test_add_car_before_day_open_is_rejected(webapp_client):
    """Смену нужно явно открыть (/api/openday) — иначе добавление машины
    отклоняется, чтобы касса не начиналась "молча" в закрытый день."""
    client, sessions_mod, _server = webapp_client
    headers = _owner_headers(client)
    sessions_mod.add_branch_worker(BRANCH, "Иван Тестов")

    r = client.post("/api/car", json={
        "branch": BRANCH, "employee": "Иван Тестов", "body_type": "sedan",
        "service_keys": ["комплекс"], "payment": "нал",
    }, headers=headers)
    assert r.status_code == 403


def test_add_car_without_services_rejected(webapp_client):
    client, sessions_mod, _server = webapp_client
    headers = _owner_headers(client)
    sessions_mod.add_branch_worker(BRANCH, "Иван Тестов")
    client.post("/api/openday", params={"branch": BRANCH}, headers=headers)

    r = client.post("/api/car", json={
        "branch": BRANCH, "employee": "Иван Тестов", "body_type": "sedan",
        "service_keys": [], "payment": "нал",
    }, headers=headers)
    assert r.status_code == 400


# ── боксы: требуют прав администратора филиала, не просто доступа ──────────

def test_boxes_add_requires_branch_admin_not_just_access(webapp_client):
    client, sessions_mod, _server = webapp_client
    sessions_mod.add_branch_worker(BRANCH, "Простой Мойщик")
    r = client.post(
        "/api/site/login",
        json={"password": PASSWORD, "name": "Простой Мойщик", "branch": BRANCH},
    )
    headers = {"X-Site-Token": r.json()["token"]}

    r = client.post("/api/boxes", json={"branch": BRANCH, "name": "Бокс 1"}, headers=headers)
    assert r.status_code == 403


def test_boxes_crud_as_owner(webapp_client):
    client, _sessions, _server = webapp_client
    headers = _owner_headers(client)

    r = client.post("/api/boxes", json={"branch": BRANCH, "name": "Бокс 1"}, headers=headers)
    assert r.status_code == 200
    box_id = r.json()["box"]["id"]

    r = client.get("/api/boxes", params={"branch": BRANCH}, headers=headers)
    # ВНИМАНИЕ: несогласованность ключей в самом API (не тест) — POST
    # /api/boxes отдаёт созданный бокс как {"id", "name"} (см.
    # add_branch_box), а GET /api/boxes отдаёт список боксов как
    # {"box": id, "name"} (см. get_branch_boxes) — разные названия ключа
    # для id в объекте и в списке. Здесь просто фиксируем поведение,
    # какое оно есть; не меняю API форму в этом проходе (вне scope).
    assert any(b["box"] == box_id for b in r.json()["boxes"])

    r = client.patch(f"/api/boxes/{BRANCH}/{box_id}", json={"name": "Бокс 1 (переименован)"}, headers=headers)
    assert r.status_code == 200

    r = client.delete(f"/api/boxes/{BRANCH}/{box_id}", headers=headers)
    assert r.status_code == 200
    assert not any(b["id"] == box_id for b in r.json()["boxes"])


# ── постоянная скидка клиента (GAP-M12) через HTTP ──────────────────────────

def test_client_discount_roundtrip_via_http(webapp_client):
    client, sessions_mod, _server = webapp_client
    headers = _owner_headers(client)
    sessions_mod.upsert_client_visit("+79990000009", "Клиент Тестов", BRANCH, "Т999ТТ", 500)

    r = client.put("/api/clients/+79990000009/discount", json={"percent": 15}, headers=headers)
    assert r.status_code == 200
    assert r.json()["discount_percent"] == 15

    r = client.get("/api/clients/+79990000009", headers=headers)
    assert r.json()["discount_percent"] == 15

    r = client.delete("/api/clients/+79990000009/discount", headers=headers)
    assert r.status_code == 200
    assert r.json()["discount_percent"] is None


def test_client_discount_out_of_range_rejected(webapp_client):
    client, sessions_mod, _server = webapp_client
    headers = _owner_headers(client)
    sessions_mod.upsert_client_visit("+79990000010", "Клиент Тестов", BRANCH, "Т999ТТ", 500)

    r = client.put("/api/clients/+79990000010/discount", json={"percent": 0}, headers=headers)
    assert r.status_code == 400

    r = client.put("/api/clients/+79990000010/discount", json={"percent": 101}, headers=headers)
    assert r.status_code == 400
