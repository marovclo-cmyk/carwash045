"""
Разовый скрипт миграции: carwash_clients.json → таблица clients в БД.

Запускать ОДИН РАЗ вручную на деплое, сразу после выкладки GAP-DB1
(этап 6) — с тем же DATA_DIR (и, в проде, тем же DATABASE_URL), что у
самого приложения.

Идемпотентен по нормализованному телефону (он же PK) — безопасно
запускать повторно, уже перенесённые карточки не дублируются
(перезаписываются теми же значениями).

Использование:
    python migrate_clients_to_db.py

Старый carwash_clients.json НЕ удаляется автоматически.
"""
import json
import os

from db import get_db_session
from db_models import ClientModel

DATA_DIR = os.getenv("DATA_DIR", os.path.expanduser("~"))
CLIENTS_JSON = os.path.join(DATA_DIR, "carwash_clients.json")


def main():
    if not os.path.exists(CLIENTS_JSON):
        print(f"⚠️ {CLIENTS_JSON} не найден — переносить нечего "
              f"(новый проект без старых данных, либо миграция уже сделана и файл убран).")
        return

    with open(CLIENTS_JSON, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        print(f"⚠️ {CLIENTS_JSON} имеет неожиданный формат (ожидался объект) — миграция прервана, ничего не изменено.")
        return

    migrated = 0
    skipped = 0
    with get_db_session() as db:
        for phone, rec in data.items():
            if not isinstance(rec, dict):
                print(f"⚠️ Пропущена некорректная карточка клиента {phone!r} (ожидался объект)")
                skipped += 1
                continue
            cars = rec.get("cars", [])
            visits = rec.get("visits", [])
            if not isinstance(cars, list) or not isinstance(visits, list):
                print(f"⚠️ Пропущена некорректная карточка клиента {phone!r}: cars/visits не список")
                skipped += 1
                continue
            fields = dict(
                name=str(rec.get("name", "")),
                cars=cars,
                visits=visits,
                discount_percent=(float(rec["discount_percent"]) if rec.get("discount_percent") is not None else None),
            )

            existing = db.get(ClientModel, str(phone))
            if existing is None:
                db.add(ClientModel(phone=str(phone), **fields))
            else:
                for key, value in fields.items():
                    setattr(existing, key, value)
            migrated += 1

    print(f"✅ Мигрировано {migrated} карточек клиентов из {CLIENTS_JSON} в БД"
          + (f" ({skipped} пропущено из-за некорректного формата)" if skipped else "") + ".")
    print(f"Файл {CLIENTS_JSON} НЕ удалён — уберите вручную после проверки, что всё перенеслось верно.")


if __name__ == "__main__":
    main()
