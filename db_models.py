"""
ORM-модели (SQLAlchemy).

Растёт по мере миграции доменов GAP-DB1 (см. PROJECT_BRAIN/CHANGELOG.md
и PROJECT_BRAIN/FUNCTIONAL_GAP_ANALYSIS.md → GAP-DB1).

Этап 1 (11.08.2026): users (белый список доступа).
Этап 2 (11.08.2026): advances (авансы сотрудников).
Этап 3 (11.08.2026): branches (конфиг филиалов: админ, сотрудники, боксы,
склад, графики).
Этап 4 (12.08.2026): payments (онлайн-оплата ЮKassa, GAP-PAY1).
Этап 5 (12.08.2026): archive (закрытые дни кассы по филиалам/датам).
Этап 6 (12.08.2026): clients (карточка клиента, история визитов).
Этап 7 (12.08.2026): bookings (журнал записи, GAP-BOX1/GAP-PAY1/публичная
витрина).
Этап 8 (12.08.2026, ФИНАЛЬНЫЙ): sessions (касса по филиалу — последний
и самый крупный домен GAP-DB1, намеренно оставлен последним). Все 8
доменов теперь на БД.
"""
from sqlalchemy import BigInteger, Boolean, Column, Float, Integer, JSON, String

from db import Base


class UserModel(Base):
    """Белый список доступа: Telegram user_id → ФИО.
    Эквивалент прежнего carwash_users.json: {"<user_id>": "Имя Фамилия"}."""

    __tablename__ = "users"

    user_id = Column(BigInteger, primary_key=True)
    name = Column(String, nullable=False)


class AdvanceModel(Base):
    """Запись о выданном авансе. Эквивалент прежнего carwash_advances.json:
    { branch: { employee_name: [ {"idx","date","amount","ts"} ] } }.

    `idx` — сквозной счётчик В ПРЕДЕЛАХ (branch, employee_name), как и в
    JSON-версии (использовался как единственный ключ удаления записи в
    api_delete_advance — сохранён без изменений, чтобы не трогать вызывающий
    код). `id` — новый суррогатный PK, наружу (в вызывающий код) не отдаётся."""

    __tablename__ = "advances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    branch = Column(String, nullable=False, index=True)
    employee_name = Column(String, nullable=False, index=True)
    idx = Column(Integer, nullable=False)
    date = Column(String, nullable=False)  # формат "%d.%m.%Y", как и раньше — см. get_employee_advances
    amount = Column(Integer, nullable=False)
    ts = Column(Float, nullable=False)


class BranchModel(Base):
    """Конфиг филиала. Эквивалент прежнего carwash_branches.json:
    { branch: {"admin","workers","admin_names","boxes","boxes_next_id",
    "stock","schedules"} }.

    В отличие от UserModel/AdvanceModel (плоские колонки), вложенные
    структуры (списки/словари) хранятся как есть в JSON-колонках, а не
    раскладываются на отдельные таблицы — тот же принцип "простая схема,
    переносимая между SQLite/Postgres", что описан в db.py, и тот же объём
    данных на филиал, что был в одном JSON-объекте. Форма данных, которую
    отдают функции sessions.py (get_branch_config и т.д.), не изменилась —
    вызывающий код не тронут.

    branch — название филиала, как и раньше первичный ключ (см. BRANCHES
    в config.py и прежний branches_config.json)."""

    __tablename__ = "branches"

    branch = Column(String, primary_key=True)
    admin = Column(BigInteger, nullable=False, default=0)
    workers = Column(JSON, nullable=False, default=list)
    admin_names = Column(JSON, nullable=False, default=list)
    boxes = Column(JSON, nullable=False, default=list)  # [{"id": int, "name": str}, ...]
    boxes_next_id = Column(Integer, nullable=False, default=1)
    stock = Column(JSON, nullable=False, default=dict)  # {key: {"qty": int, "min_qty": int}}
    schedules = Column(JSON, nullable=False, default=dict)  # {name: {"work","rest","start"}}


class PaymentModel(Base):
    """Запись об онлайн-платеже (GAP-PAY1, ЮKassa/мок-провайдер).
    Эквивалент прежнего carwash_payments.json: { payment_id: {запись} }.

    id — НЕ суррогатный: это id, который выдаёт сам провайдер платежей
    (payment_provider.py) — "mock_<hex>" в мок-режиме, id платежа ЮKassa
    в боевом; в обоих случаях уникален глобально, поэтому здесь остаётся
    первичным ключом, как и раньше был ключом верхнего уровня в JSON.
    Все остальные колонки — плоские, без вложенных структур (в отличие
    от BranchModel), т.к. одна запись платежа и так плоская в JSON."""

    __tablename__ = "payments"

    id = Column(String, primary_key=True)
    branch = Column(String, nullable=False, index=True)
    purpose = Column(String, nullable=False)  # "advance" | "car"
    booking_id = Column(Integer, nullable=True)
    car_num = Column(Integer, nullable=True)
    amount = Column(Integer, nullable=False)
    description = Column(String, nullable=False, default="")
    phone = Column(String, nullable=False, default="")
    client_name = Column(String, nullable=False, default="")
    status = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    confirmation_url = Column(String, nullable=False, default="")
    applied = Column(Boolean, nullable=False, default=False)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    paid_at = Column(String, nullable=True)


class ArchiveDayModel(Base):
    """Закрытый день кассы одного филиала. Эквивалент прежнего
    carwash_archive.json: { branch: { date: day_dict } }.

    Композитный первичный ключ (branch, date) — как и раньше, день
    однозначно определялся этой парой (verbatim ключи верхнего/второго
    уровня словаря). Содержимое дня (`day`) хранится ЦЕЛИКОМ одной
    JSON-колонкой, а не раскладывается на поля: форма дня не полностью
    фиксирована — обычный день (`save_to_archive`) содержит
    cars/products/expenses/incomes/loyalty/admin_percent/admin_name/
    fixed_rates/admin_fixed_rate, но `overwrite_archive_day` (ручная
    правка админом, восстановление из PDF через pdf_importer.py) может
    записать туда любой словарь той же общей формы — раскладка на
    строгую схему потребовала бы отдельно защищать оба этих пути от
    рассинхронизации, что не требовалось ни одним известным паттерном
    доступа (всегда читается/пишется день целиком)."""

    __tablename__ = "archive_days"

    branch = Column(String, primary_key=True)
    date = Column(String, primary_key=True)  # формат "%d.%m.%Y"
    day = Column(JSON, nullable=False)


class ClientModel(Base):
    """Карточка клиента. Эквивалент прежнего carwash_clients.json:
    { normalized_phone: {"phone","name","cars":[...],"visits":[...],
    "discount_percent"?} }.

    phone (нормализованный, см. sessions.normalize_phone) — как и раньше
    первичный ключ верхнего уровня словаря. cars — плоский список строк
    (ярлыки/номера машин клиента), visits — список словарей визитов;
    оба хранятся JSON-колонкой целиком, а не раскладываются на отдельные
    таблицы: тот же принцип, что у BranchModel/ArchiveDayModel — форма
    записи визита не полностью фиксирована (service/time/paid/status
    появились позже как необязательные поля, см. upsert_client_visit),
    а обращение всегда идёт к списку визитов клиента целиком, отдельная
    таблица визитов не даёт здесь никакой практической пользы.

    discount_percent — Float, nullable. Отсутствие постоянной скидки
    выражается значением NULL (а не отсутствием колонки, как раньше было
    отсутствием ключа в JSON) — вызывающий код уже везде обращается к
    этому полю через .get()-подобное чтение, которое одинаково видит и
    отсутствующий ключ, и ключ со значением None."""

    __tablename__ = "clients"

    phone = Column(String, primary_key=True)
    name = Column(String, nullable=False, default="")
    cars = Column(JSON, nullable=False, default=list)
    visits = Column(JSON, nullable=False, default=list)
    discount_percent = Column(Float, nullable=True)


class BookingModel(Base):
    """Запись (журнал записи). Эквивалент прежнего carwash_bookings.json:
    { branch: { "ДД.ММ.ГГГГ": [ {запись}, ... ] } }.

    id — НЕ автоинкремент БД, а тот же сквозной счётчик "максимум
    существующих id + 1", что был в JSON-версии (см. sessions.create_booking):
    запись должна однозначно адресоваться (GET/PATCH/DELETE по id) без
    указания филиала/даты, а branch/date у записи МЕНЯЮТСЯ (перенос записи
    на другую дату/в другой филиал, см. update_booking) — переезжающий
    surrogate id тут неуместен, id обязан быть стабилен всё время жизни
    записи. branch/date остаются плоскими колонками (а не составным PK,
    как у ArchiveDayModel) именно потому, что они изменяемы.

    В отличие от BranchModel/ArchiveDayModel/ClientModel, форма самой
    записи полностью фиксирована (см. create_booking/BookingIn в
    webapp/server.py) — поэтому почти все поля плоские колонки; JSON
    остаётся только у по-настоящему списковых/словарных полей
    (service_keys/custom_services/product_keys/payment_split/prepayment),
    тем же принципом, что у cars/visits в ClientModel.

    prepayment — заполняется отдельно, уже после создания записи, при
    успешной оплате аванса через GAP-PAY1 (см.
    sessions.apply_payment_success → update_booking(..., prepayment=...)):
    {"amount","status","payment_id","paid_at"}. NULL, пока предоплаты не
    было — так же, как раньше отсутствие ключа "prepayment" в JSON-записи."""

    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, autoincrement=False)
    branch = Column(String, nullable=False, index=True)
    date = Column(String, nullable=False, index=True)  # формат "%d.%m.%Y"
    box = Column(Integer, nullable=False)
    start_time = Column(String, nullable=False)  # "ЧЧ:ММ"
    end_time = Column(String, nullable=False)
    employee = Column(String, nullable=False, default="")
    body_type = Column(String, nullable=False, default="")
    car = Column(String, nullable=False, default="")
    service_keys = Column(JSON, nullable=False, default=list)
    custom_services = Column(JSON, nullable=False, default=list)  # [{"name","price","percent"}]
    product_keys = Column(JSON, nullable=False, default=list)
    price = Column(Integer, nullable=False, default=0)
    price_calc = Column(Integer, nullable=False, default=0)
    price_override = Column(Integer, nullable=True)
    payment = Column(String, nullable=False, default="")
    payment_split = Column(JSON, nullable=True)  # {method: amount} | None
    comment = Column(String, nullable=False, default="")
    phone = Column(String, nullable=False, default="")
    client_name = Column(String, nullable=False, default="")
    status = Column(String, nullable=False, default="waiting")
    car_num = Column(Integer, nullable=True)  # проставляется при конвертации записи в машину кассы
    prepayment = Column(JSON, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class SessionModel(Base):
    """Касса смены одного филиала. Эквивалент прежнего
    carwash_sessions.json: { branch: session_dict }.

    branch — первичный ключ, как и раньше был ключом верхнего уровня
    словаря (касса ведётся ПО ФИЛИАЛУ, не по пользователю — см. докстринг
    sessions.py). Всё содержимое смены (`data`) хранится ЦЕЛИКОМ одной
    JSON-колонкой, тем же принципом, что и ArchiveDayModel.day: форма
    смены НЕ полностью фиксирована — помимо базовых полей из
    _empty_session (date/branch/cars/products/expenses/incomes/loyalty/
    admin_percent/admin_name/day_open), в неё во время смены дописываются
    необязательные ключи разными вызывающими сторонами (fixed_rates/
    admin_fixed_rate — задним числом правка ставок, см.
    patch_fixed_rates; actual_cash/cash_discrepancy — сверка кассы при
    закрытии дня, см. webapp/server.py) — раскладка на строгую схему
    потребовала бы синхронно защищать все эти места от рассинхронизации,
    что не даёт никакой практической пользы: смена всегда читается/
    пишется целиком через get_session()/save_sessions() в память процесса
    и обратно (см. sessions.py, раздел "СЕССИИ").

    В отличие от остальных 7 доменов, здесь сохранён прежний паттерн
    "весь кэш в памяти процесса, сброс на диск (теперь — в БД) целиком
    при каждом изменении" (см. sessions.py: load_sessions/save_sessions/
    глобальный словарь sessions) — переписывать десятки мест по всему
    проекту (bot.py, handlers/*, webapp/server.py), которые мутируют
    словарь смены в памяти напрямую и затем вызывают save_sessions(), на
    построчный ORM-паттерн ("row.field = value" на каждую мутацию) было
    бы рискованным переписыванием далеко за пределы sessions.py ради
    этого последнего этапа — вместо этого сменился только сам бэкенд
    load_sessions()/save_sessions() (JSON-файл → БД), контракт для всех
    вызывающих сторон не изменился."""

    __tablename__ = "sessions"

    branch = Column(String, primary_key=True)
    data = Column(JSON, nullable=False)
