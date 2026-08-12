"""
Разовый скрипт миграции: carwash_bookings.json → таблица bookings в БД.

Запускать ОДИН РАЗ вручную на деплое, сразу после выкладки GAP-DB1
(этап 7) — с тем же DATA_DIR (и, в проде, тем же DATABASE_URL), что у
самого приложения.

Идемпотентен по id записи (он же PK) — безопасно запускать повторно,
уже перенесённые записи не дублируются (перезаписываются теми же
значениями).

branch/date, под которыми запись лежала в JSON (ключи двух верхних
уровней словаря), берутся как источник истины и переносятся именно они
— а не одноимённые поля внутри самой записи, которые обязаны совпадать,
но лежат в куда более старых записях реже проверялись на согласованность
между собой.

Использование:
    python migrate_bookings_to_db.py

Старый carwash_bookings.json НЕ удаляется автоматически.
"""
import json
import os

from db import get_db_session
from db_models import BookingModel

DATA_DIR = os.getenv("DATA_DIR", os.path.expanduser("~"))
BOOKINGS_JSON = os.path.join(DATA_DIR, "carwash_bookings.json")


def main():
    if not os.path.exists(BOOKINGS_JSON):
        print(f"⚠️ {BOOKINGS_JSON} не найден — переносить нечего "
              f"(новый проект без старых данных, либо миграция уже сделана и файл убран).")
        return

    with open(BOOKINGS_JSON, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        print(f"⚠️ {BOOKINGS_JSON} имеет неожиданный формат (ожидался объект) — миграция прервана, ничего не изменено.")
        return

    migrated = 0
    skipped = 0
    with get_db_session() as db:
        for branch, by_date in data.items():
            if not isinstance(by_date, dict):
                print(f"⚠️ Пропущен некорректный филиал в записях {branch!r} (ожидался объект)")
                skipped += 1
                continue
            for date, items in by_date.items():
                if not isinstance(items, list):
                    print(f"⚠️ Пропущен некорректный день записей {branch!r}/{date!r} (ожидался список)")
                    skipped += 1
                    continue
                for rec in items:
                    if not isinstance(rec, dict) or not isinstance(rec.get("id"), int):
                        print(f"⚠️ Пропущена некорректная запись в {branch!r}/{date!r} (нет числового id)")
                        skipped += 1
                        continue
                    fields = dict(
                        branch=branch,
                        date=date,
                        box=int(rec.get("box") or 0),
                        start_time=str(rec.get("start_time", "")),
                        end_time=str(rec.get("end_time", "")),
                        employee=str(rec.get("employee", "")),
                        body_type=str(rec.get("body_type", "")),
                        car=str(rec.get("car", "")),
                        service_keys=rec.get("service_keys") or [],
                        custom_services=rec.get("custom_services") or [],
                        product_keys=rec.get("product_keys") or [],
                        price=int(rec.get("price") or 0),
                        price_calc=int(rec.get("price_calc") or 0),
                        price_override=(int(rec["price_override"]) if rec.get("price_override") is not None else None),
                        payment=str(rec.get("payment", "")),
                        payment_split=rec.get("payment_split") or None,
                        comment=str(rec.get("comment", "")),
                        phone=str(rec.get("phone", "")),
                        client_name=str(rec.get("client_name", "")),
                        status=str(rec.get("status") or "waiting"),
                        car_num=(int(rec["car_num"]) if rec.get("car_num") is not None else None),
                        prepayment=rec.get("prepayment") or None,
                        created_at=str(rec.get("created_at") or ""),
                        updated_at=str(rec.get("updated_at") or ""),
                    )

                    existing = db.get(BookingModel, rec["id"])
                    if existing is None:
                        db.add(BookingModel(id=rec["id"], **fields))
                    else:
                        for key, value in fields.items():
                            setattr(existing, key, value)
                    migrated += 1

    print(f"✅ Мигрировано {migrated} записей из {BOOKINGS_JSON} в БД"
          + (f" ({skipped} пропущено из-за некорректного формата)" if skipped else "") + ".")
    print(f"Файл {BOOKINGS_JSON} НЕ удалён — уберите вручную после проверки, что всё перенеслось верно.")


if __name__ == "__main__":
    main()
