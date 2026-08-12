"""
Разовый скрипт миграции: carwash_branches.json → таблица branches в БД.

Запускать ОДИН РАЗ вручную на деплое, сразу после выкладки GAP-DB1
(этап 3) — с тем же DATA_DIR (и, в проде, тем же DATABASE_URL), что у
самого приложения.

Идемпотентен — безопасно запускать повторно (upsert по названию филиала,
не дублирует записи).

Использование:
    python migrate_branches_to_db.py

Старый carwash_branches.json НЕ удаляется автоматически — после проверки,
что миграция прошла (сверить филиалы/сотрудников/боксы/остатки), можно
убрать вручную.
"""
import json
import os

from db import get_db_session
from db_models import BranchModel

DATA_DIR = os.getenv("DATA_DIR", os.path.expanduser("~"))
BRANCHES_JSON = os.path.join(DATA_DIR, "carwash_branches.json")


def main():
    if not os.path.exists(BRANCHES_JSON):
        print(f"⚠️ {BRANCHES_JSON} не найден — переносить нечего "
              f"(новый проект без старых данных, либо миграция уже сделана и файл убран).")
        return

    with open(BRANCHES_JSON, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        print(f"⚠️ {BRANCHES_JSON} имеет неожиданный формат (ожидался объект) — миграция прервана, ничего не изменено.")
        return

    migrated = 0
    skipped = 0
    with get_db_session() as db:
        for branch, cfg in data.items():
            if not isinstance(cfg, dict):
                print(f"⚠️ Пропущен некорректный филиал {branch!r} (ожидался объект)")
                skipped += 1
                continue

            boxes = cfg.get("boxes", [])
            if not isinstance(boxes, list):
                boxes = []
            boxes = [{"id": int(b["id"]), "name": str(b.get("name") or f"Бокс {b['id']}")} for b in boxes]

            boxes_next_id = cfg.get("boxes_next_id")
            if not isinstance(boxes_next_id, int):
                # Тот же расчёт по умолчанию, что раньше делал JSON-вариант
                # load_branches_config() при отсутствии этого поля.
                boxes_next_id = max([b["id"] for b in boxes], default=0) + 1

            workers = cfg.get("workers", [])
            workers = [str(w) for w in workers] if isinstance(workers, list) else []

            admin_names = cfg.get("admin_names", [])
            admin_names = [str(n) for n in admin_names] if isinstance(admin_names, list) else []

            stock = cfg.get("stock", {})
            if not isinstance(stock, dict):
                stock = {}
            stock = {
                str(k): {"qty": int(v.get("qty", 0)), "min_qty": int(v.get("min_qty", 0))}
                for k, v in stock.items() if isinstance(v, dict)
            }

            schedules = cfg.get("schedules", {})
            if not isinstance(schedules, dict):
                schedules = {}
            schedules = {
                str(k): {"work": int(v.get("work", 0)), "rest": int(v.get("rest", 0)), "start": str(v.get("start", ""))}
                for k, v in schedules.items() if isinstance(v, dict)
            }

            try:
                admin = int(cfg.get("admin", 0) or 0)
            except (TypeError, ValueError):
                admin = 0

            existing = db.get(BranchModel, branch)
            if existing is None:
                existing = BranchModel(branch=branch)
                db.add(existing)
            existing.admin = admin
            existing.workers = workers
            existing.admin_names = admin_names
            existing.boxes = boxes
            existing.boxes_next_id = boxes_next_id
            existing.stock = stock
            existing.schedules = schedules
            migrated += 1

    print(f"✅ Мигрировано {migrated} филиалов из {BRANCHES_JSON} в БД"
          + (f" ({skipped} пропущено из-за некорректного формата)" if skipped else "") + ".")
    print(f"Файл {BRANCHES_JSON} НЕ удалён — уберите вручную после проверки, что всё перенеслось верно.")


if __name__ == "__main__":
    main()
