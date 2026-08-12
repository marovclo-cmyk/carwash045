"""
Пресеты услуг ("Стандартный набор для седана" и т.п.) — чтобы не выбирать
чекбоксы каждый раз заново. Хранится отдельным JSON-файлом.
"""
import json
import os
import threading

_LOCK = threading.Lock()
_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets.json")


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


def list_presets(branch: str) -> list:
    return _load().get(branch, [])


def add_preset(branch: str, name: str, service_keys: list, custom_services: list) -> list:
    with _LOCK:
        data = _load()
        presets = data.setdefault(branch, [])
        presets = [p for p in presets if p["name"] != name]  # перезаписываем, если имя совпало
        presets.append({"name": name, "service_keys": service_keys, "custom_services": custom_services})
        data[branch] = presets
        _save(data)
        return presets


def delete_preset(branch: str, name: str) -> list:
    with _LOCK:
        data = _load()
        presets = [p for p in data.get(branch, []) if p["name"] != name]
        data[branch] = presets
        _save(data)
        return presets
