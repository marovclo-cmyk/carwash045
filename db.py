"""
Слой подключения к базе данных (SQLAlchemy) — GAP-DB1.

Прод: Postgres, подключение через переменную окружения DATABASE_URL
(Railway-плагин Postgres создаёт её автоматически при подключении БД к
сервису). Railway/Heroku отдают URL в форме "postgres://...", а SQLAlchemy
2.0 с драйвером psycopg (v3) ожидает схему "postgresql+psycopg://..." —
схема нормализуется в _resolve_database_url().

Дев/тесты (DATABASE_URL не задан): SQLite-файл "<DATA_DIR>/carwash.db".
Это НЕ равнозначно продовой БД (другой диалект SQL), но для доменов,
переносимых на БД в рамках GAP-DB1, схема нарочно держится простой и
переносимой между SQLite/Postgres. DATA_DIR — та же переменная окружения,
что уже использует sessions.py для JSON-хранилища и что уже подменяется
в tests/conftest.py на временную директорию каждого теста — поэтому
тестовая изоляция БД работает по той же схеме, без отдельной DB-фикстуры.

Важно: engine НЕ кэшируется на момент импорта модуля (в отличие от путей
к JSON-файлам в sessions.py). sessions.py перезагружается в тестах через
importlib.reload() при подмене DATA_DIR, а db.py — нет (на него просто
ссылаются из sessions.py). Если бы engine создавался один раз при первом
импорте db.py, все тесты делили бы один и тот же SQLite-файл от первого
теста, что сломало бы изоляцию. Поэтому get_engine() пересчитывает
DATABASE_URL/DATA_DIR при каждом вызове и кэширует engine по значению URL
(_engine_for_url) — разные тестовые DATA_DIR получают разные файлы и
разные engine, один и тот же прод-URL — один и тот же engine.
"""
import os
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()


def _resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://") and "+psycopg" not in url:
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url
    data_dir = os.getenv("DATA_DIR", os.path.expanduser("~"))
    os.makedirs(data_dir, exist_ok=True)
    return f"sqlite:///{os.path.join(data_dir, 'carwash.db')}"


@lru_cache(maxsize=None)
def _engine_for_url(url: str):
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite:"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    import db_models  # noqa: F401 — регистрирует модели в Base.metadata перед create_all
    Base.metadata.create_all(bind=engine)
    return engine


def get_engine():
    """engine для ТЕКУЩЕГО значения DATABASE_URL/DATA_DIR — см. докстринг
    модуля про причину пересчёта при каждом вызове вместо кэша на импорте."""
    return _engine_for_url(_resolve_database_url())


@contextmanager
def get_db_session():
    """Контекстный менеджер: одна транзакция на блок `with`. Коммитит при
    успешном выходе, откатывает при исключении — тот же контракт, что и
    файловая блокировка `_update_json_locked` в sessions.py (или всё
    применилось, или ничего)."""
    session = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
