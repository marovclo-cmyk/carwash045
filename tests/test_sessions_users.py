"""
Тесты белого списка пользователей после переноса на БД (GAP-DB1, этап 1).

sessions_mod уже изолирует DATA_DIR на временную директорию теста (см.
conftest.py) — так как db.py пересчитывает DATABASE_URL/DATA_DIR при
каждом обращении (get_engine(), см. докстринг db.py), каждый тест
автоматически получает свой изолированный SQLite-файл без отдельной
DB-фикстуры. Реальный carwash_users.json / прод-БД не затрагиваются.

Контракт функций (форма данных: dict {str(user_id): "Имя"}) идентичен
дореформенному JSON-хранилищу — вызывающий код (handlers/admin.py,
webapp/server.py) не менялся и не должен был.
"""


def test_load_users_empty_by_default(sessions_mod):
    assert sessions_mod.load_users() == {}


def test_add_user_then_load(sessions_mod):
    sessions_mod.add_user(111, "Иван Иванов")
    assert sessions_mod.load_users() == {"111": "Иван Иванов"}


def test_add_user_keys_are_strings(sessions_mod):
    """JSON-объекты всегда имели строковые ключи — БД-версия должна вести
    себя так же, чтобы users.get(str(uid)) в остальном коде не сломался."""
    sessions_mod.add_user(222, "Пётр Петров")
    users = sessions_mod.load_users()
    assert list(users.keys()) == ["222"]
    assert isinstance(list(users.keys())[0], str)


def test_add_user_updates_name_for_same_id(sessions_mod):
    sessions_mod.add_user(333, "Старое Имя")
    sessions_mod.add_user(333, "Новое Имя")
    users = sessions_mod.load_users()
    assert users == {"333": "Новое Имя"}


def test_remove_user_returns_true_when_existed(sessions_mod):
    sessions_mod.add_user(444, "Кто-то")
    assert sessions_mod.remove_user(444) is True
    assert sessions_mod.load_users() == {}


def test_remove_user_returns_false_when_absent(sessions_mod):
    assert sessions_mod.remove_user(999999) is False


def test_multiple_users_independent(sessions_mod):
    sessions_mod.add_user(1, "Первый")
    sessions_mod.add_user(2, "Второй")
    sessions_mod.remove_user(1)
    assert sessions_mod.load_users() == {"2": "Второй"}


def test_save_users_full_overwrite(sessions_mod):
    sessions_mod.add_user(5, "Будет стёрт")
    sessions_mod.save_users({"10": "Единственный после перезаписи"})
    assert sessions_mod.load_users() == {"10": "Единственный после перезаписи"}
