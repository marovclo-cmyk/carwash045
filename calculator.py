from config import SALARY_ADMIN, get_blended_percent

def round_salary(amount: float) -> int:
    """
    Округление по последним двум цифрам суммы:
      0–30  → вниз до XX00   (1830 → 1800)
      31–50 → вверх до XX50  (1831 → 1850)
      51–70 → вниз до XX50   (1861 → 1850)
      71–99 → вверх до (XX+1)00 (1871 → 1900)
    """
    amount = int(round(amount))
    base = (amount // 100) * 100
    rem = amount % 100
    if rem <= 30:
        return base
    elif rem <= 50:
        return base + 50
    elif rem <= 70:
        return base + 50
    else:
        return base + 100

def calculate_summary(session: dict) -> dict:
    cars     = session.get("cars", [])
    products = session.get("products", [])
    expenses = session.get("expenses", [])
    incomes  = session.get("incomes", [])
    loyalty  = session.get("loyalty", [])

    # Скидки по машинам
    loyalty_by_car = {}
    for l in loyalty:
        loyalty_by_car[l["car_num"]] = loyalty_by_car.get(l["car_num"], 0) + l["discount"]

    # car["price"] хранит ПОЛНУЮ стоимость услуги (без вычета скидки).
    # Скидка лояльности хранится отдельно в session["loyalty"].
    # Поэтому чтобы получить реально поступившие деньги — вычитаем скидку.
    def car_amounts(c):
        """Возвращает {метод: сумма} для машины — с учётом раздельной оплаты."""
        split = c.get("payment_split")
        if split:
            return {k.lower(): v for k, v in split.items() if v}
        return {c["payment"].lower(): c["price"]}

    def sum_method(cars_list, keys):
        total = 0
        for c in cars_list:
            for method, amount in car_amounts(c).items():
                if method in keys:
                    total += amount
        return total

    raw_cash   = sum_method(cars, ["нал", "наличка"])
    raw_visa   = sum_method(cars, ["visa", "виза"])
    raw_beznal = sum_method(cars, ["безнал", "петрон", "petron"])

    # Для отображения — сколько скидок пришлось на какой тип оплаты
    # (для машин с раздельной оплатой скидка относится к оплате наличными по умолчанию)
    def loyalty_method(c):
        split = c.get("payment_split")
        if split:
            for k in split:
                if k.lower() in ["нал", "наличка"]:
                    return "нал"
            return list(split.keys())[0].lower()
        return c["payment"].lower()

    loyalty_cash   = sum(loyalty_by_car.get(c["num"], 0) for c in cars if loyalty_method(c) in ["нал","наличка"])
    loyalty_visa   = sum(loyalty_by_car.get(c["num"], 0) for c in cars if loyalty_method(c) in ["visa","виза"])
    loyalty_beznal = sum(loyalty_by_car.get(c["num"], 0) for c in cars if loyalty_method(c) in ["безнал","петрон","petron"])

    # Реально поступившие деньги (после вычета скидки)
    cash   = raw_cash   - loyalty_cash
    visa   = raw_visa   - loyalty_visa
    beznal = raw_beznal - loyalty_beznal

    products_cash   = sum(p["price"] for p in products if p["payment"].lower() in ["нал","наличка"])
    products_visa   = sum(p["price"] for p in products if p["payment"].lower() in ["visa","виза"])
    products_beznal = sum(p["price"] for p in products if p["payment"].lower() in ["безнал","петрон","petron"])
    total_products  = products_cash + products_visa + products_beznal

    cash   += products_cash
    visa   += products_visa
    beznal += products_beznal
    total  = cash + visa + beznal

    total_loyalty = sum(l["discount"] for l in loyalty)

    washer_totals   = {}
    washer_salaries = {}
    for car in cars:
        emp      = car["employee"]
        # Зарплата — от полной суммы (car["price"] уже полная, до вычета скидки)
        full_sum = car["price"]
        washer_totals[emp] = washer_totals.get(emp, 0) + full_sum

        breakdown = car.get("price_breakdown")
        if breakdown:
            salary_part = sum(v["price"] * v["percent"] for v in breakdown.values())
        else:
            pct         = get_blended_percent(car.get("service_keys") or [car.get("service_key", "")])
            salary_part = full_sum * pct
        washer_salaries[emp] = washer_salaries.get(emp, 0) + salary_part

    washer_salaries = {e: round_salary(v) for e, v in washer_salaries.items()}

    total_washers = sum(washer_totals.values())
    admin_base    = total_washers + total_products
    admin_salary  = round_salary(admin_base * session.get("admin_percent", SALARY_ADMIN))

    # Фиксированная ставка администратора ("Ставка") — на случай пустого
    # отчёта: за смену не было ни одной машины (или касса не велась), но
    # администратор дежурил и должен получить фикс (по умолчанию 1000₽).
    # Работает так же, как фикс-ставка мойщика: добавляется СВЕРХУ, отдельно
    # от процента с выручки.
    admin_fixed_rate = session.get("admin_fixed_rate", 0)
    if admin_fixed_rate:
        admin_salary += admin_fixed_rate

    # ── Универсальная разбивка заработка по РОЛЯМ (для агрегации по сотруднику) ──
    # role_earnings: {employee_name: {role_label: amount}}.
    # Один и тот же человек может в этот же день фигурировать сразу в нескольких
    # ролях (например, был мойщиком и в этот же день дежурил администратором) —
    # оба заработка складываются в один словарь по имени, а не создают "разных"
    # сотрудников. Чтобы добавить новую роль в будущем (кассир, детейлер и т.д.),
    # достаточно посчитать её заработок и вызвать _add_role_earning ещё раз —
    # остальной код (агрегация, отчёты, mini-app) трогать не нужно.
    role_earnings: dict[str, dict[str, float]] = {}

    def _add_role_earning(name: str, role: str, amount: float):
        if not name or not amount:
            return
        role_earnings.setdefault(name, {})
        role_earnings[name][role] = role_earnings[name].get(role, 0) + amount

    for emp, sal in washer_salaries.items():
        _add_role_earning(emp, "мойщик", sal)

    admin_name = session.get("admin_name") or ""
    if admin_name:
        _add_role_earning(admin_name, "администратор", admin_salary)

    # Фиксированная ставка ("Ставка") — на случай, если мойщик за день не помыл
    # ни одной машины (или просто работает по фиксу), администратор вручную
    # проставляет сумму. Она добавляется к зарплате СВЕРХУ и НЕ входит в базу
    # для расчёта процента администратора (это не реальная выручка от машин).
    fixed_rates = session.get("fixed_rates", {})
    for emp, amount in fixed_rates.items():
        washer_totals.setdefault(emp, 0)
        washer_salaries[emp] = washer_salaries.get(emp, 0) + amount
        _add_role_earning(emp, "мойщик", amount)

    def income_amounts(inc):
        """Возвращает {метод: сумма} для дохода — с учётом раздельной оплаты."""
        split = inc.get("payment_split")
        if split:
            return {k.lower(): v for k, v in split.items() if v}
        return {inc.get("payment", "нал").lower(): inc["amount"]}

    def sum_income_method(incomes_list, keys):
        total = 0
        for inc in incomes_list:
            for method, amount in income_amounts(inc).items():
                if method in keys:
                    total += amount
        return total

    income_cash   = sum_income_method(incomes, ["нал", "наличка"])
    income_visa   = sum_income_method(incomes, ["visa", "виза"])
    income_beznal = sum_income_method(incomes, ["безнал", "петрон", "petron"])

    cash   += income_cash
    visa   += income_visa
    beznal += income_beznal
    total  = cash + visa + beznal

    total_expenses = sum(e["amount"] for e in expenses)
    expenses_str   = "; ".join(f"{e['name']} - {e['amount']}" for e in expenses) or "нет"

    total_incomes = income_cash + income_visa + income_beznal
    incomes_str   = "; ".join(f"{i['name']} - {i['amount']}" for i in incomes) or "нет"

    return {
        "total": total, "grand_total": total + total_loyalty,
        "cash": cash, "visa": visa, "beznal": beznal,
        "total_loyalty": total_loyalty,
        "loyalty_cash": loyalty_cash, "loyalty_visa": loyalty_visa, "loyalty_beznal": loyalty_beznal,
        "total_products": total_products,
        "washer_totals": washer_totals, "washer_salaries": washer_salaries,
        "fixed_rates": fixed_rates,
        "admin_salary": admin_salary,
        "admin_fixed_rate": admin_fixed_rate,
        "role_earnings": role_earnings,
        "total_expenses": total_expenses, "expenses_str": expenses_str,
        "total_incomes": total_incomes, "incomes_str": incomes_str,
        "income_cash": income_cash, "income_visa": income_visa, "income_beznal": income_beznal,
        "remainder": cash - total_expenses,
    }
