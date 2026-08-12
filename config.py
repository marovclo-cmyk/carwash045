import os
from dotenv import load_dotenv
load_dotenv()

TOKEN     = os.getenv("BOT_TOKEN")
OWNER_ID  = int(os.getenv("OWNER_ID", "485678784"))
PROXY_URL = os.getenv("PROXY_URL", "").strip()

# ── ФИЛИАЛЫ ────────────────────────────────────────────────────────────────
# Реальные админы филиалов и список сотрудников хранятся в branches_config.json
# (см. sessions.py) и могут меняться владельцем через бота.
# Здесь только список названий филиалов по умолчанию.
BRANCHES = [
    "Карла Маркса",
    "Балаклавская",
    "Генерала Васильева",
    "Данилова",
    "Евпаторийка",
]

SALARY_ADMIN  = 0.10
SALARY_WASHER = 0.30  # фолбэк, если % услуги не определён

# ── ТИПЫ КУЗОВА ────────────────────────────────────────────────────────────
BODY_TYPES = {
    "sedan":     "Седан",
    "crossover": "Кроссовер",
    "suv":       "Внедорожник",
    "bus":       "Микроавтобус",
}
BODY_TYPE_ORDER = ["sedan", "crossover", "suv", "bus"]

# ── УСЛУГИ ─────────────────────────────────────────────────────────────────
# prices: либо {body_type: цена} на каждый тип кузова,
#         либо одно число — единая цена для всех типов кузова.
# percent: % сотрудника от этой услуги (для расчёта зарплаты).
SERVICES = {
    "комплекс": {
        "name": "Комплексная мойка",
        "prices": {"sedan": 2000, "crossover": 2500, "suv": 3000, "bus": 3500},
        "percent": 0.30,
    },
    "детейлинг": {
        "name": "Детейлинг мойка",
        "prices": {"sedan": 7000, "crossover": 8000, "suv": 9000, "bus": 10000},
        "percent": 0.30,
    },
    "ручная": {
        "name": "Ручная мойка + коврики",
        "prices": {"sedan": 1100, "crossover": 1200, "suv": 1300, "bus": 1500},
        "percent": 0.30,
    },
    "2фазная": {
        "name": "2х фазная мойка",
        "prices": {"sedan": 1500, "crossover": 1800, "suv": 2000, "bus": 2500},
        "percent": 0.30,
    },
    "3фазная": {
        "name": "3х фазная мойка",
        "prices": {"sedan": 1800, "crossover": 2100, "suv": 2300, "bus": 2800},
        "percent": 0.30,
    },
    "салон": {
        "name": "Влажная уборка + пылесос салона",
        "prices": {"sedan": 1200, "crossover": 1300, "suv": 1500, "bus": 1800},
        "percent": 0.30,
    },
    "воск": {
        "name": "Консервант кузова",
        "prices": {"sedan": 500, "crossover": 500, "suv": 600, "bus": 700},
        "percent": 0.30,
    },
    "твердвоск": {
        "name": "Твёрдый воск / полимер",
        "prices": {"sedan": 6000, "crossover": 7000, "suv": 8000, "bus": 9000},
        "percent": 0.40,
    },
    "пластик": {
        "name": "Обработка пластика",
        "prices": {"sedan": 500, "crossover": 500, "suv": 700, "bus": 800},
        "percent": 0.30,
    },
    "очисткакожи": {
        "name": "Очистка кожи салона",
        "prices": {"sedan": 6000, "crossover": 7000, "suv": 8000, "bus": 10000},
        "percent": 0.40,
    },
    "кожа": {
        "name": "Обработка кожаных элементов",
        "prices": {"sedan": 700, "crossover": 800, "suv": 900, "bus": 1200},
        "percent": 0.30,
    },
    "экспресска": {
        "name": "Экспресс химчистка",
        "prices": {"sedan": 15000, "crossover": 17000, "suv": 19000, "bus": 22000},
        "percent": 0.40,
    },
    # Единая цена для всех типов кузова
    "озон": {
        "name": "Озонирование салона",
        "prices": 2000,
        "percent": 0.30,
    },
    "мошка": {
        "name": "Удаление следов насекомых",
        "prices": 500,
        "percent": 0.30,
    },
    "нагар": {
        "name": "Удаление нагара с дисков",
        "prices": 1000,
        "percent": 0.30,
    },
    "химкузов": {
        "name": "Химчистка кузова",
        "prices": 5000,
        "percent": 0.30,
    },
    "химподкапот": {
        "name": "Химчистка подкапота",
        "prices": 8000,
        "percent": 0.30,
    },
    "антидождь": {
        "name": "Антидождь",
        "prices": 6000,
        "percent": 0.40,
    },
}

# ── ТОВАРЫ (не услуги мойки — продаются отдельно, не влияют на зарплату мойщиков) ──
# Идут в общую кассу. Админ получает свои 10% и с них (см. calculator.py).
PRODUCTS = {
    "olympea":     {"name": "Духи Shine Systems Black Line Olympea",   "price": 1500},
    "belle":       {"name": "Духи Black Line Belle",                  "price": 1500},
    "coco":        {"name": "Духи Blackline Coco",                    "price": 1500},
    "blackvanila": {"name": "Духи Black Vanila Blackline",             "price": 1500},
    "coolwater":   {"name": "Духи Blackline Cool Water",               "price": 1500},
    "invictus":    {"name": "Духи Blackline Invictus",                 "price": 1500},
    "siena":       {"name": "Духи Siena Blackline",                    "price": 1500},
    "whitevanila": {"name": "Духи White Vanila Blackline",             "price": 1500},
}

PAYMENT_TYPES = ["нал", "visa", "безнал"]


def get_product_price(product_key: str) -> int:
    p = PRODUCTS.get(product_key)
    return p["price"] if p else 0


def get_service_price(service_key: str, body_type: str) -> int:
    """Цена услуги для конкретного типа кузова (по прайсу)."""
    svc = SERVICES.get(service_key)
    if not svc:
        return 0
    prices = svc["prices"]
    if isinstance(prices, dict):
        return prices.get(body_type, 0)
    return prices


def get_service_percent(service_key: str) -> float:
    svc = SERVICES.get(service_key)
    return svc["percent"] if svc else SALARY_WASHER


def get_blended_percent(service_keys: list[str]) -> float:
    """Средний % по комбо-услугам (цена делится поровну между ними)."""
    keys = [k for k in service_keys if k in SERVICES]
    if not keys:
        return SALARY_WASHER
    return sum(get_service_percent(k) for k in keys) / len(keys)


def services_label(service_keys: list[str]) -> str:
    keys = [k for k in service_keys if k in SERVICES]
    if not keys:
        return ""
    percents = sorted({get_service_percent(k) for k in keys})
    return "+".join(f"{int(p*100)}%" for p in percents)


def services_display_name(service_keys: list[str]) -> str:
    return "+".join(SERVICES[k]["name"] for k in service_keys if k in SERVICES) or "—"


def services_short_name(service_keys: list[str]) -> str:
    """Короткое название (псевдоним/ключ) для компактных мест вроде PDF-таблицы."""
    return "+".join(k for k in service_keys if k in SERVICES) or "—"
