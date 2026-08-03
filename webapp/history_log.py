"""
Журнал действий по кассе: кто и когда добавил/изменил/удалил машину.
Хранится отдельным JSON-файлом, чтобы не трогать существующий sessions.py.
"""
import json
import os
import threading
from datetime import datetime

_LOCK = threading.Lock()
_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")


def _load() -> dict:
    if not os.path.exists(_PATH):
        return {}
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _PATH)


def log_action(branch: str, action: str, actor_id: int, actor_name: str, details: str = "") -> None:
    """action: 'add' | 'edit' | 'delete' | 'newday' и т.д."""
    with _LOCK:
        data = _load()
        entries = data.setdefault(branch, [])
        entries.append({
            "action": action,
            "actor_id": actor_id,
            "actor_name": actor_name or "—",
            "details": details,
            "at": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        })
        # держим последние 500 записей на филиал, чтобы файл не рос бесконечно
        data[branch] = entries[-500:]
        _save(data)


def get_history(branch: str, limit: int = 100) -> list:
    data = _load()
    return list(reversed(data.get(branch, [])))[:limit]
