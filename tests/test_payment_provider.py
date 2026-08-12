"""
Тесты payment_provider.py (GAP-PAY1).

Ключевое поведение:
- без боевых ключей ЮKassa в окружении по умолчанию активен
  MockYooKassaProvider (is_mock_active() == True);
- как только заданы ОБА ключа (YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY) —
  get_provider() отдаёт YooKassaProvider, is_mock_active() == False;
- одного из двух ключей недостаточно — это всё ещё мок (защита от
  случайной наполовину заполненной конфигурации).
"""
import importlib
import sys


def _reload_payment_provider():
    import payment_provider
    module = sys.modules.get("payment_provider")
    if module is None:
        module = importlib.import_module("payment_provider")
    else:
        module = importlib.reload(module)
    return module


def test_mock_provider_active_by_default(monkeypatch):
    monkeypatch.delenv("YOOKASSA_SHOP_ID", raising=False)
    monkeypatch.delenv("YOOKASSA_SECRET_KEY", raising=False)
    pp = _reload_payment_provider()
    assert pp.is_mock_active() is True
    assert isinstance(pp.get_provider(), pp.MockYooKassaProvider)


def test_real_provider_requires_both_keys(monkeypatch):
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "123")
    monkeypatch.delenv("YOOKASSA_SECRET_KEY", raising=False)
    pp = _reload_payment_provider()
    assert pp.is_mock_active() is True
    assert isinstance(pp.get_provider(), pp.MockYooKassaProvider)


def test_real_provider_active_when_both_keys_set(monkeypatch):
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "123")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "secret")
    pp = _reload_payment_provider()
    try:
        assert pp.is_mock_active() is False
        provider = pp.get_provider()
        assert isinstance(provider, pp.YooKassaProvider)
        assert provider.shop_id == "123"
        assert provider.secret_key == "secret"
    finally:
        # не оставляем боевые ключи висеть в окружении для следующих тестов
        monkeypatch.delenv("YOOKASSA_SHOP_ID", raising=False)
        monkeypatch.delenv("YOOKASSA_SECRET_KEY", raising=False)
        _reload_payment_provider()


def test_mock_create_payment_shape():
    pp = _reload_payment_provider()
    provider = pp.MockYooKassaProvider()
    resp = provider.create_payment(500, "Предоплата", {"branch": "x"})
    assert resp["status"] == "pending"
    assert resp["id"].startswith("mock_")
    assert resp["id"] in resp["confirmation_url"]
