"""
Общие фикстуры для тестов.

sessions.py читает путь к данным (DATA_DIR) и вычисляет пути к JSON-файлам
хранилища один раз при импорте модуля. Чтобы каждый тест работал со своим
чистым хранилищем (а не с реальными carwash_*.json на диске разработчика/
CI), фикстура `sessions_mod` подменяет DATA_DIR через переменную окружения
на временную директорию и перезагружает модуль — это пересчитывает все пути
(SESSIONS_FILE/ARCHIVE_FILE/BRANCHES_FILE/...) и сбрасывает module-level
кэш `_branches_cache` в None. Ни один тест не должен трогать реальные файлы
хранилища проекта.
"""
import importlib
import os
import sys

import pytest

# tests/ не является пакетом (нет __init__.py), поэтому pytest не добавляет
# корень проекта (carwash045/) в sys.path автоматически — добавляем сами,
# иначе `import sessions`/`import calculator` в тестах не найдётся.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


@pytest.fixture
def sessions_mod(tmp_path, monkeypatch):
    """Возвращает свежеперезагруженный модуль sessions.py, изолированный от
    реального хранилища (пишет только во временную директорию теста)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import sessions  # noqa: F401 (может быть уже импортирован раньше)
    module = sys.modules.get("sessions")
    if module is None:
        module = importlib.import_module("sessions")
    else:
        module = importlib.reload(module)
    yield module


@pytest.fixture
def employee_stats_mod(sessions_mod):
    """employee_stats.py делает `from sessions import ... sessions as
    _live_sessions, ...` — это ПРИВЯЗКА конкретных объектов на момент
    импорта. Если просто перезагрузить sessions (см. sessions_mod), у
    employee_stats останутся старые ссылки (на старый DATA_DIR/старый
    словарь sessions). Поэтому здесь employee_stats перезагружается ПОСЛЕ
    sessions_mod — тогда его импорты пересвязываются на свежий модуль."""
    import employee_stats
    module = importlib.reload(employee_stats)
    yield module


@pytest.fixture
def bot_admin_mod(sessions_mod):
    """handlers/admin.py — та же проблема привязки имён при импорте, что и
    у employee_stats_mod: `from sessions import get_session, load_users,
    ...` нужно пересвязать после изоляции sessions.py."""
    import handlers.admin as admin_mod
    module = importlib.reload(admin_mod)
    yield module


@pytest.fixture
def bot_cars_mod(sessions_mod, bot_admin_mod):
    """handlers/cars.py импортирует `get_branch_workers` из sessions и
    `get_current_branch` из handlers.admin — оба нужно пересвязать после
    изоляции хранилища, иначе parse_car_from_text() будет читать список
    сотрудников из чужого/старого DATA_DIR."""
    import handlers.cars as cars_mod
    module = importlib.reload(cars_mod)
    yield module


@pytest.fixture
def webapp_client(tmp_path, monkeypatch):
    """TestClient для webapp/server.py, полностью изолированный от
    реального хранилища и от настоящего Telegram-бота:
    - DATA_DIR подменяется на временную директорию теста (та же схема, что
      и в sessions_mod);
    - SITE_PASSWORD/SITE_OWNER_NAMES задаются тестовыми значениями, чтобы
      не зависеть от .env разработчика/CI и не пускать тестовые токены на
      реальный сайт;
    - BOT_TOKEN подменяется на синтаксически валидный (но нерабочий)
      токен — иначе notify.py падает на импорте (`Bot(token=TOKEN)`
      проверяет формат токена при создании, даже если сообщение никогда
      не отправляется).
    config.py/notify.py/webapp.auth_web/webapp.server читают эти значения
    (или производные от sessions.DATA_DIR пути) один раз при импорте —
    поэтому все они перезагружаются заново, в порядке зависимостей."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SITE_PASSWORD", "test-password")
    monkeypatch.setenv("SITE_OWNER_NAMES", "Тестовый Владелец")
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST-TOKENAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")

    import config
    importlib.reload(config)
    import sessions
    sessions_module = importlib.reload(sessions)
    import notify
    importlib.reload(notify)
    import webapp.auth_web as auth_web
    importlib.reload(auth_web)
    import webapp.server as server
    server_module = importlib.reload(server)

    from fastapi.testclient import TestClient
    client = TestClient(server_module.app)
    yield client, sessions_module, server_module
