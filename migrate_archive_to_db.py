"""
Разовый скрипт миграции: carwash_archive.json → таблица archive_days в БД.

Запускать ОДИН РАЗ вручную на деплое, сразу после выкладки GAP-DB1
(этап 5) — с тем же DATA_DIR (и, в проде, тем же DATABASE_URL), что у
самого приложения.

Идемпотентен по (branch, date) — безопасно запускать повторно, уже
перенесённые дни не дублируются (перезаписываются теми же значениями).

Использование:
    python migrate_archive_to_db.py

Старый carwash_archive.json НЕ удаляется автоматически.
"""
import json
import os

from db import get_db_session
from db_models import ArchiveDayModel

DATA_DIR = os.getenv("DATA_DIR", os.path.expanduser("~"))
ARCHIVE_JSON = os.path.join(DATA_DIR, "carwash_archive.json")


def main():
    if not os.path.exists(ARCHIVE_JSON):
        print(f"⚠️ {ARCHIVE_JSON} не найден — переносить нечего "
              f"(новый проект без старых данных, либо миграция уже сделана и файл убран).")
        return

    with open(ARCHIVE_JSON, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        print(f"⚠️ {ARCHIVE_JSON} имеет неожиданный формат (ожидался объект) — миграция прервана, ничего не изменено.")
        return

    migrated = 0
    skipped = 0
    with get_db_session() as db:
        for branch, by_date in data.items():
            if not isinstance(by_date, dict):
                print(f"⚠️ Пропущен некорректный филиал в архиве {branch!r} (ожидался объект)")
                skipped += 1
                continue
            for date, day in by_date.items():
                if not isinstance(day, dict):
                    print(f"⚠️ Пропущен некорректный день архива {branch!r}/{date!r} (ожидался объект)")
                    skipped += 1
                    continue
                existing = db.get(ArchiveDayModel, (branch, date))
                if existing is None:
                    db.add(ArchiveDayModel(branch=branch, date=date, day=day))
                else:
                    existing.day = day
                migrated += 1

    print(f"✅ Мигрировано {migrated} дней архива из {ARCHIVE_JSON} в БД"
          + (f" ({skipped} пропущено из-за некорректного формата)" if skipped else "") + ".")
    print(f"Файл {ARCHIVE_JSON} НЕ удалён — уберите вручную после проверки, что всё перенеслось верно.")


if __name__ == "__main__":
    main()
