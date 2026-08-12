"""
Разовый скрипт миграции: carwash_sessions.json → таблица sessions в БД
(GAP-DB1, этап 8 — ФИНАЛЬНЫЙ домен).

Запускать ОДИН РАЗ вручную на деплое, сразу после выкладки этого этапа —
с тем же DATA_DIR (и, в проде, тем же DATABASE_URL), что у самого
приложения. ВАЖНО: запускать ДО первого старта run_all.py после деплоя
(load_sessions() при старте читает уже из БД — если сначала стартовать
приложение, а потом мигрировать, текущая незакрытая смена филиала будет
не видна процессу, пока его не перезапустить).

Идемпотентен по названию филиала (он же PK) — безопасно запускать
повторно, уже перенесённые смены не дублируются (перезаписываются теми
же значениями).

Использование:
    python migrate_sessions_to_db.py

Старый carwash_sessions.json НЕ удаляется автоматически.
"""
import json
import os

from db import get_db_session
from db_models import SessionModel

DATA_DIR = os.getenv("DATA_DIR", os.path.expanduser("~"))
SESSIONS_JSON = os.path.join(DATA_DIR, "carwash_sessions.json")


def main():
    if not os.path.exists(SESSIONS_JSON):
        print(f"⚠️ {SESSIONS_JSON} не найден — переносить нечего "
              f"(новый проект без старых данных, либо миграция уже сделана и файл убран).")
        return

    with open(SESSIONS_JSON, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        print(f"⚠️ {SESSIONS_JSON} имеет неожиданный формат (ожидался объект) — миграция прервана, ничего не изменено.")
        return

    migrated = 0
    skipped = 0
    with get_db_session() as db:
        for branch, session in data.items():
            if not isinstance(session, dict):
                print(f"⚠️ Пропущена некорректная смена филиала {branch!r} (ожидался объект)")
                skipped += 1
                continue
            existing = db.get(SessionModel, branch)
            if existing is None:
                db.add(SessionModel(branch=branch, data=session))
            else:
                existing.data = session
            migrated += 1

    print(f"✅ Мигрировано {migrated} смен из {SESSIONS_JSON} в БД"
          + (f" ({skipped} пропущено из-за некорректного формата)" if skipped else "") + ".")
    print(f"Файл {SESSIONS_JSON} НЕ удалён — уберите вручную после проверки, что всё перенеслось верно.")


if __name__ == "__main__":
    main()
