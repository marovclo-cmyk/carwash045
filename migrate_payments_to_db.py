"""
Разовый скрипт миграции: carwash_payments.json → таблица payments в БД.

Запускать ОДИН РАЗ вручную на деплое, сразу после выкладки GAP-DB1
(этап 4) — с тем же DATA_DIR (и, в проде, тем же DATABASE_URL), что у
самого приложения.

Идемпотентен по id платежа (он же PK — выдаётся провайдером, не нами) —
безопасно запускать повторно, уже перенесённые записи не дублируются
(перезаписываются теми же значениями).

Использование:
    python migrate_payments_to_db.py

Старый carwash_payments.json НЕ удаляется автоматически.
"""
import json
import os

from db import get_db_session
from db_models import PaymentModel

DATA_DIR = os.getenv("DATA_DIR", os.path.expanduser("~"))
PAYMENTS_JSON = os.path.join(DATA_DIR, "carwash_payments.json")


def main():
    if not os.path.exists(PAYMENTS_JSON):
        print(f"⚠️ {PAYMENTS_JSON} не найден — переносить нечего "
              f"(новый проект без старых данных, либо миграция уже сделана и файл убран).")
        return

    with open(PAYMENTS_JSON, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        print(f"⚠️ {PAYMENTS_JSON} имеет неожиданный формат (ожидался объект) — миграция прервана, ничего не изменено.")
        return

    migrated = 0
    skipped = 0
    with get_db_session() as db:
        for payment_id, rec in data.items():
            if not isinstance(rec, dict):
                print(f"⚠️ Пропущен некорректный платёж {payment_id!r} (ожидался объект)")
                skipped += 1
                continue
            try:
                fields = dict(
                    branch=str(rec["branch"]),
                    purpose=str(rec["purpose"]),
                    booking_id=(int(rec["booking_id"]) if rec.get("booking_id") is not None else None),
                    car_num=(int(rec["car_num"]) if rec.get("car_num") is not None else None),
                    amount=int(rec["amount"]),
                    description=str(rec.get("description", "")),
                    phone=str(rec.get("phone", "")),
                    client_name=str(rec.get("client_name", "")),
                    status=str(rec.get("status", "pending")),
                    provider=str(rec.get("provider", "")),
                    confirmation_url=str(rec.get("confirmation_url", "")),
                    applied=bool(rec.get("applied", False)),
                    created_at=str(rec.get("created_at", "")),
                    updated_at=str(rec.get("updated_at", "")),
                    paid_at=(str(rec["paid_at"]) if rec.get("paid_at") is not None else None),
                )
            except (KeyError, TypeError, ValueError):
                print(f"⚠️ Пропущен некорректный платёж {payment_id!r}: {rec!r}")
                skipped += 1
                continue

            existing = db.get(PaymentModel, str(payment_id))
            if existing is None:
                db.add(PaymentModel(id=str(payment_id), **fields))
            else:
                for key, value in fields.items():
                    setattr(existing, key, value)
            migrated += 1

    print(f"✅ Мигрировано {migrated} платежей из {PAYMENTS_JSON} в БД"
          + (f" ({skipped} пропущено из-за некорректного формата)" if skipped else "") + ".")
    print(f"Файл {PAYMENTS_JSON} НЕ удалён — уберите вручную после проверки, что всё перенеслось верно.")


if __name__ == "__main__":
    main()
