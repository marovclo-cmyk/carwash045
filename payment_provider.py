"""
GAP-PAY1 — абстракция платёжного провайдера (эквайринг).

Выбор провайдера определяется окружением, вызывающий код (sessions.py,
webapp/server.py) провайдера не выбирает и не знает деталей API:

- Заданы YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY → используется боевой/
  тестовый YooKassaProvider (реальные вызовы REST API ЮKassa,
  https://api.yookassa.ru/v3/payments).
- Ключей нет (по умолчанию, пока владелец их не выдал) →
  MockYooKassaProvider — та же форма ответа (id/status/confirmation_url),
  но без реального эквайринга: confirmation_url ведёт на локальную
  страницу-заглушку (см. GET /pay/{id} в webapp/server.py), а
  подтверждение оплаты имитируется отдельным dev-эндпоинтом
  POST /api/payments/{id}/mock-confirm — он работает, только пока активен
  мок-провайдер (см. is_mock_active()), так что подделать подтверждение
  боевого платежа через него нельзя.

Когда владелец предоставит боевые ключи ЮKassa — их достаточно прописать
в переменных окружения (.env / Railway variables). Код переключится
автоматически при следующем импорте модуля, без изменений в вызывающей
стороне.
"""
import os
import uuid
import logging

log = logging.getLogger("payment_provider")

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "").strip()
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "").strip()
YOOKASSA_API_URL = "https://api.yookassa.ru/v3/payments"

# Публичный HTTPS-адрес деплоя (для return_url / ссылок на мок-страницу
# оплаты). Без него мок-ссылки будут относительными путями — рабочими
# внутри Mini App/сайта, но не открывающимися напрямую вне Telegram.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")


class PaymentProviderError(Exception):
    """Ошибка на стороне платёжного провайдера (сеть/API/конфигурация)."""


class MockYooKassaProvider:
    """Заглушка на время, пока нет боевых ключей ЮKassa. Повторяет форму
    ответа реального провайдера (id/status/confirmation_url), чтобы
    вызывающий код не отличал мок от боевой интеграции."""
    name = "mock_yookassa"

    def create_payment(self, amount: int, description: str, metadata: dict) -> dict:
        payment_id = f"mock_{uuid.uuid4().hex[:24]}"
        return {
            "id": payment_id,
            "status": "pending",
            "confirmation_url": f"{PUBLIC_BASE_URL}/pay/{payment_id}",
        }

    def get_payment(self, payment_id: str) -> dict:
        # Мок не хранит своё состояние — статус ведёт sessions.py
        # (carwash_payments.json). Метод существует только для совпадения
        # интерфейса с YooKassaProvider.
        return {"id": payment_id, "status": "pending"}


class YooKassaProvider:
    """Боевая/тестовая интеграция с ЮKassa (REST API v3, Basic Auth
    shopId:secretKey). Используется, только если оба ключа заданы —
    см. get_provider()."""
    name = "yookassa"

    def __init__(self, shop_id: str, secret_key: str):
        self.shop_id = shop_id
        self.secret_key = secret_key

    def create_payment(self, amount: int, description: str, metadata: dict) -> dict:
        import requests
        idempotence_key = str(uuid.uuid4())
        payload = {
            "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": PUBLIC_BASE_URL or "https://t.me",
            },
            "capture": True,
            "description": (description or "Оплата CarWash")[:128],
            "metadata": metadata or {},
        }
        try:
            resp = requests.post(
                YOOKASSA_API_URL, json=payload,
                auth=(self.shop_id, self.secret_key),
                headers={"Idempotence-Key": idempotence_key},
                timeout=15,
            )
        except requests.RequestException as e:
            log.error("YooKassa create_payment: сетевая ошибка: %s", e)
            raise PaymentProviderError("Не удалось связаться с ЮKassa") from e
        if resp.status_code >= 300:
            log.error("YooKassa create_payment failed: %s %s", resp.status_code, resp.text)
            raise PaymentProviderError(f"ЮKassa вернула ошибку {resp.status_code}")
        data = resp.json()
        return {
            "id": data["id"],
            "status": data["status"],
            "confirmation_url": (data.get("confirmation") or {}).get("confirmation_url", ""),
        }

    def get_payment(self, payment_id: str) -> dict:
        import requests
        try:
            resp = requests.get(
                f"{YOOKASSA_API_URL}/{payment_id}",
                auth=(self.shop_id, self.secret_key), timeout=15,
            )
        except requests.RequestException as e:
            raise PaymentProviderError("Не удалось связаться с ЮKassa") from e
        if resp.status_code >= 300:
            raise PaymentProviderError(f"ЮKassa вернула ошибку {resp.status_code}")
        data = resp.json()
        return {"id": data["id"], "status": data["status"]}


def is_mock_active() -> bool:
    """True, пока боевые ключи ЮKassa не заданы — определяет, доступен ли
    dev-эндпоинт mock-confirm и какой вид имеет confirmation_url."""
    return not (YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY)


def get_provider():
    if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
        return YooKassaProvider(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
    return MockYooKassaProvider()
