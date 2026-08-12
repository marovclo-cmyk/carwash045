"""
Разовый скрипт миграции: carwash_users.json → таблица users в БД.

Запускать ОДИН РАЗ вручную на деплое, сразу после выкладки GAP-DB1
(этап 1) — с тем же DATA_DIR (и, в проде, тем же DATABASE_URL), что у
самого приложения, иначе скрипт и приложение будут смотреть на разные
данные. На Railway: тот же сервис/окружение, что и веб-процесс.

Идемпотентен — безопасно запускать повторно (upsert по user_id, не
дублирует записи).

Использование:
    python migrate_users_to_db.py

Старый carwash_users.json НЕ удаляется автоматически — после проверки,
что миграция прошла (сверить количество/содержимое), можно убрать вручную.
"""
import json
import os

from db import get_db_session
from db_models import UserModel

DATA_DIR = os.getenv("DATA_DIR", os.path.expanduser("~"))
USERS_JSON = os.path.join(DATA_DIR, "carwash_users.json")


def main():
    if not os.path.exists(USERS_JSON):
        print(f"⚠️ {USERS_JSON} не найден — переносить нечего "
              f"(новый проект без старых данных, либо миграция уже сделана и файл убран).")
        return

    with open(USERS_JSON, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        print(f"⚠️ {USERS_JSON} имеет неожиданный формат (ожидался объект) — миграция прервана, ничего не изменено.")
        return

    migrated = 0
    skipped = 0
    with get_db_session() as db:
        for uid, name in data.items():
            try:
                uid_int = int(uid)
            except (TypeError, ValueError):
                print(f"⚠️ Пропущен некорректный user_id={uid!r} (не число)")
                skipped += 1
                continue
            existing = db.get(UserModel, uid_int)
            if existing:
                existing.name = name
            else:
                db.add(UserModel(user_id=uid_int, name=name))
            migrated += 1

    print(f"✅ Мигрировано {migrated} пользователей из {USERS_JSON} в БД"
          + (f" ({skipped} пропущено из-за некорректного id)" if skipped else "") + ".")
    print(f"Файл {USERS_JSON} НЕ удалён — уберите вручную после проверки, что всё перенеслось верно.")


if __name__ == "__main__":
    main()
