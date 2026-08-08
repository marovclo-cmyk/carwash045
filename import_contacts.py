"""
Одноразовый импорт клиентов из экспорта контактов телефона (iCloud/vCard),
уже сконвертированного в contacts_seed.json ([[phone, name], ...]).

Запускается автоматически при старте (см. run_all.py), но выполняет
реальный импорт только один раз — после успешного импорта создаётся
файл-маркер в DATA_DIR, и при последующих запусках функция сразу
выходит, ничего не делая. Сам импорт в sessions.import_contacts()
дополнительно безопасен и при повторном запуске: уже существующие
номера (реальные клиенты или ранее импортированные) не перезаписываются.
"""
import json
import os

import sessions

SEED_FILE = os.path.join(os.path.dirname(__file__), "contacts_seed.json")
MARKER_FILE = os.path.join(sessions.DATA_DIR, ".contacts_imported_icloud_v1")


def run_import_once():
    if os.path.exists(MARKER_FILE):
        return
    if not os.path.exists(SEED_FILE):
        return
    try:
        with open(SEED_FILE, encoding="utf-8") as f:
            contacts = json.load(f)
        result = sessions.import_contacts(contacts)
        print(
            f"📇 Импорт контактов: добавлено {result['added']}, "
            f"уже были {result['skipped_existing']}, "
            f"пропущено невалидных {result['skipped_invalid']}",
            flush=True,
        )
    except Exception as e:
        print(f"❌ Импорт контактов не удался: {e}", flush=True)
        return
    with open(MARKER_FILE, "w", encoding="utf-8") as f:
        f.write("done")


if __name__ == "__main__":
    run_import_once()
