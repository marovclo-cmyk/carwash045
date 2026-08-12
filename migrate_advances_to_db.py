"""
Разовый скрипт миграции: carwash_advances.json → таблица advances в БД.

Запускать ОДИН РАЗ вручную на деплое, сразу после выкладки GAP-DB1
(этап 2) — с тем же DATA_DIR (и, в проде, тем же DATABASE_URL), что у
самого приложения.

Идемпотентен по (branch, employee_name, idx) — безопасно запускать
повторно, уже перенесённые записи не дублируются (перезаписываются теми
же значениями).

Использование:
    python migrate_advances_to_db.py

Старый carwash_advances.json НЕ удаляется автоматически.
"""
import json
import os

from db import get_db_session
from db_models import AdvanceModel

DATA_DIR = os.getenv("DATA_DIR", os.path.expanduser("~"))
ADVANCES_JSON = os.path.join(DATA_DIR, "carwash_advances.json")


def main():
    if not os.path.exists(ADVANCES_JSON):
        print(f"⚠️ {ADVANCES_JSON} не найден — переносить нечего.")
        return

    with open(ADVANCES_JSON, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        print(f"⚠️ {ADVANCES_JSON} имеет неожиданный формат (ожидался объект) — миграция прервана, ничего не изменено.")
        return

    migrated = 0
    skipped = 0
    with get_db_session() as db:
        for branch, by_name in data.items():
            if not isinstance(by_name, dict):
                continue
            for name, entries in by_name.items():
                if not isinstance(entries, list):
                    continue
                for e in entries:
                    try:
                        idx = int(e["idx"])
                        amount = int(e["amount"])
                        date = str(e["date"])
                        ts = float(e.get("ts", 0))
                    except (KeyError, TypeError, ValueError):
                        print(f"⚠️ Пропущена некорректная запись аванса: {branch}/{name}: {e!r}")
                        skipped += 1
                        continue
                    existing = db.query(AdvanceModel).filter(
                        AdvanceModel.branch == branch,
                        AdvanceModel.employee_name == name,
                        AdvanceModel.idx == idx,
                    ).first()
                    if existing:
                        existing.date = date
                        existing.amount = amount
                        existing.ts = ts
                    else:
                        db.add(AdvanceModel(
                            branch=branch, employee_name=name, idx=idx,
                            date=date, amount=amount, ts=ts,
                        ))
                    migrated += 1

    print(f"✅ Мигрировано {migrated} записей об авансах из {ADVANCES_JSON} в БД"
          + (f" ({skipped} пропущено из-за некорректного формата)" if skipped else "") + ".")
    print(f"Файл {ADVANCES_JSON} НЕ удалён — уберите вручную после проверки, что всё перенеслось верно.")


if __name__ == "__main__":
    main()
