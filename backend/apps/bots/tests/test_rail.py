"""Отчёт о вагонах → клиент, товары, цены, дубли → заказ, отгрузка, склад, долг."""
import importlib
from datetime import timedelta
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.bots.models import BotClientProfile, WhatsAppBotSettings
from apps.bots.parsing import parse_rail_report
from apps.bots.rail import (
    conduct_rail_report,
    remember_client_profile,
    resolve_report,
)
from apps.bots.tests.samples import (
    CONDUCT_CODES,
    OWNER_BAGS,
    OWNER_DAY,
    OWNER_REPORT,
    OWNER_WAGONS,
    issue_codes,
    move_to_retail,
    report,
    stock_bags,
    train_order,
)
from apps.catalog.models import ClientPrice, Product, ProductAlias
from apps.catalog.services import remember_product_alias
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.notifications.models import Notification
from apps.orders.backdate import backdate_moment
from apps.orders.models import Order
from apps.shipments.models import ShipmentWagon
from apps.warehouse.models import StockItem, StockMovement

pytestmark = pytest.mark.django_db

def _resolve(text=OWNER_REPORT):
    return resolve_report(parse_rail_report(text))


def _header(day, client_name="ООО OSIYO NAV NIHOL"):
    return f"{day:%d.%m.%y} Узбекистан {client_name}"


def _shipped_before(client, product, days, **fields):
    """Вагонный заказ, отгруженный за ``days`` дней до отчёта владельца."""
    return train_order(client, product, shipped_at=backdate_moment(OWNER_DAY - timedelta(days=days)), **fields)


# --- словари -------------------------------------------------------------------------------------


def test_alias_and_profile_store_layout_free_keys(client, product):
    alias = ProductAlias.objects.get(product=product)
    # Сравнение — по ключу, людям и «Отправить отчёт» — как в отчёте.
    assert (alias.code, alias.spelling, alias.display_code) == ("Д1C", "Д1с", "Д1с")
    with pytest.raises(IntegrityError), transaction.atomic():
        ProductAlias.objects.create(code="д1c", product=product)  # тот же код латиницей

    profile = BotClientProfile.objects.create(name=' OOO  "Osiyo" ', client=client, currency="USD")

    assert (profile.name, profile.name_key) == ('OOO "Osiyo"', "OOOOSIYO")


def test_remembered_product_code_resolves_the_next_report(client, price, product, boss):
    ProductAlias.objects.all().delete()
    assert issue_codes(_resolve()) == ["product_unknown"]

    alias = remember_product_alias(" д1c ", product, boss)

    assert (alias.code, alias.product, alias.created_by) == ("Д1C", product, boss)
    assert _resolve().ok


def test_code_of_another_live_product_moves_only_on_request(product, boss, manager):
    ProductAlias.objects.all().delete()
    old = Product.objects.create(name="Д1 старый", weight_kg="50")
    first = remember_product_alias("Д1с", old, manager)

    # Бот списывает склад по словарю: молча перенаправленный код списал бы не тот товар.
    with pytest.raises(ValidationError) as caught:
        remember_product_alias("Д1c", product, boss)
    assert caught.value.detail["code"] == "alias_taken"
    assert ProductAlias.objects.get().product == old

    again = remember_product_alias("Д1c", product, boss, move=True)

    assert again.pk == first.pk
    assert (again.product, again.created_by, again.spelling) == (product, manager, "Д1c")
    assert ProductAlias.objects.count() == 1


def test_code_of_an_archived_product_is_free(product, boss):
    ProductAlias.objects.all().delete()
    old = Product.objects.create(name="Д1 старый", weight_kg="50")
    remember_product_alias("Д1с", old, boss)
    old.is_active = False
    old.save()

    assert remember_product_alias("Д1с", product, boss).product == product


def test_same_code_again_keeps_the_latest_spelling(product, boss):
    ProductAlias.objects.all().delete()
    remember_product_alias(" д1c ", product, boss)

    alias = remember_product_alias("Д1с", product, boss)

    assert (ProductAlias.objects.count(), alias.code, alias.spelling) == (1, "Д1C", "Д1с")


@pytest.mark.parametrize("code", ["", " - ", "«»"])
def test_product_code_must_have_letters_or_digits(product, boss, code):
    with pytest.raises(ValidationError) as caught:
        remember_product_alias(code, product, boss)

    assert caught.value.detail["code"] == "alias_empty"


def test_overlong_product_code_is_an_input_error_not_a_crash(product, boss):
    with pytest.raises(ValidationError) as caught:
        remember_product_alias("Д" * 65, product, boss)

    assert caught.value.detail["code"] == "alias_too_long"
    assert "64" in str(caught.value.detail["detail"])
    assert remember_product_alias("Д" * 64, product, boss).code == "Д" * 64


def test_archived_product_cannot_get_a_code(product, boss):
    product.is_active = False
    product.save()

    with pytest.raises(ValidationError) as caught:
        remember_product_alias("Д1с", product, boss)

    assert caught.value.detail["code"] == "product_archived"


def test_remembered_client_name_resolves_with_its_currency(client, product, department, boss):
    client.company_name = "Осиё Нав"
    client.save()
    ClientPrice.objects.create(client=client, product=product, currency="KZT", price="3700")
    assert issue_codes(_resolve()) == ["client_unknown"]

    profile = remember_client_profile("ООО  OSIYO NAV NIHOL", client, "KZT", boss)

    assert (profile.name, profile.currency, profile.created_by) == ("ООО OSIYO NAV NIHOL", "KZT", boss)
    resolved = _resolve()
    assert resolved.ok, resolved.issues
    assert (resolved.client, resolved.currency, resolved.profile) == (client, "KZT", profile)


def test_remembering_a_known_client_name_updates_it(client, department, boss, manager):
    first = remember_client_profile("OOO Osiyo Nav Nihol", client, "USD", manager)
    other = Client.objects.create_with_user(first_name="Другой", phone="3", department=department)

    again = remember_client_profile("ООО OSIYO NAV NIHOL", other, "KZT", boss)

    assert again.pk == first.pk
    assert (again.client, again.currency, again.name, again.created_by) == (
        other, "KZT", "ООО OSIYO NAV NIHOL", manager)


@pytest.mark.parametrize(
    ("name", "currency", "code"),
    [
        ("", "USD", "alias_empty"),
        ("ООО OSIYO", "RUB", "invalid_currency"),
        ("ООО " + "O" * 200, "USD", "alias_too_long"),
    ],
)
def test_client_profile_needs_a_name_and_a_known_currency(client, boss, name, currency, code):
    with pytest.raises(ValidationError) as caught:
        remember_client_profile(name, client, currency, boss)

    assert caught.value.detail["code"] == code


# --- resolve_report -------------------------------------------------------------------------------


def test_owner_report_resolves_to_client_price_and_16320_bags(client, product, price, department):
    resolved = _resolve()

    assert resolved.issues == ()
    assert resolved.ok
    assert resolved.client == client
    assert (resolved.currency, resolved.department) == ("USD", "export")
    assert [wagon.number for wagon in resolved.wagons] == list(OWNER_WAGONS)
    assert {(wagon.product, wagon.bags, wagon.weight_kg) for wagon in resolved.wagons} == {
        (product, 1360, Decimal("68000"))
    }
    (item,) = resolved.items
    assert (item.product, item.code, item.bags, item.wagons, item.unit_price) == (
        product, "Д1с", OWNER_BAGS, 12, Decimal("7.50"))
    assert item.amount == Decimal("122400.00")
    assert resolved.total_bags == OWNER_BAGS
    assert resolved.warnings == ()


def test_profile_maps_report_name_to_client_and_currency(client, product, department):
    client.company_name = "Осиё Нав"
    client.save()
    BotClientProfile.objects.create(name="ООО  OSIYO NAV NIHOL", client=client, currency="KZT")
    ClientPrice.objects.create(client=client, product=product, currency="KZT", price="3700")

    resolved = _resolve()

    assert resolved.ok, resolved.issues
    assert (resolved.client, resolved.currency) == (client, "KZT")
    assert resolved.items[0].unit_price == Decimal("3700.00")


def test_client_name_matches_across_layouts_and_quotes(client, product, price):
    client.company_name = 'OOO "Osiyo Nav Nihol"'  # латинские «OOO», кавычки
    client.save()

    assert _resolve().client == client


def test_client_matches_by_person_name_without_company(product, department):
    person = Client.objects.create_with_user(
        first_name="Бахтиёр", last_name="Каримов", phone="1", currency="USD", department=department)
    ClientPrice.objects.create(client=person, product=product, currency="USD", price="7.50")

    resolved = _resolve(report(f"Д1с-{OWNER_WAGONS[0]}-68 тн", header="сб 19.09.26 Узбекистан Бахтиёр Каримов"))

    assert resolved.ok, resolved.issues
    assert resolved.client == person


def test_unknown_client_needs_review(product):
    resolved = _resolve()

    assert issue_codes(resolved) == ["client_unknown"]
    assert resolved.issues[0].subject == "ООО OSIYO NAV NIHOL"
    assert resolved.client is None
    assert not resolved.ok


def test_two_clients_with_the_same_name_need_review(client, product, department):
    Client.objects.create_with_user(
        first_name="Двойник", phone="2", company_name="OOO Osiyo Nav Nihol", department=department)

    resolved = _resolve()

    assert issue_codes(resolved) == ["client_ambiguous"]
    assert resolved.client is None


def test_client_without_department_needs_review(client, product, price):
    client.department = None
    client.save()

    assert issue_codes(_resolve()) == ["client_department_missing"]


def test_client_with_disabled_department_needs_review(client, product, price, department):
    department.is_active = False
    department.save()

    assert issue_codes(_resolve()) == ["client_department_inactive"]


def test_unknown_product_code_is_reported_once(client, price):
    ProductAlias.objects.all().delete()

    resolved = _resolve()

    assert issue_codes(resolved) == ["product_unknown"]
    assert resolved.issues[0].subject == "Д1с"
    assert resolved.wagons == ()


def test_product_code_matches_latin_c(client, product, price):
    resolved = _resolve(report(f"Д1c-{OWNER_WAGONS[0]}-68 тн"))

    assert resolved.ok, resolved.issues
    assert resolved.items[0].product == product


def test_archived_product_needs_review(client, product, price):
    product.is_active = False
    product.save()

    assert issue_codes(_resolve()) == ["product_archived"]


def test_missing_client_price_needs_review(client, product):
    resolved = _resolve()

    assert issue_codes(resolved) == ["price_missing"]
    assert "USD" in resolved.issues[0].message


def test_price_far_from_last_wagon_order_needs_review(client, product, price):
    previous = _shipped_before(client, product, 30, unit_price="6.00")

    resolved = _resolve()

    assert issue_codes(resolved) == ["price_mismatch"]
    assert resolved.issues[0].order_id == previous.pk
    assert "25%" in resolved.issues[0].message
    assert resolved.items[0].reference_price == Decimal("6.00")


def test_price_within_tolerance_of_last_wagon_order_is_fine(client, product, price):
    _shipped_before(client, product, 60, unit_price="5.00")
    latest = _shipped_before(client, product, 30, unit_price="7.00")

    resolved = _resolve()

    assert resolved.ok, resolved.issues
    assert resolved.items[0].reference_order_id == latest.pk


def test_legacy_shipment_without_date_does_not_become_the_reference(client, product, price):
    latest = _shipped_before(client, product, 30, unit_price="7.00")
    train_order(client, product, unit_price="1.00")  # отгружен, а отгрузки с датой нет

    resolved = _resolve()

    assert resolved.ok, resolved.issues
    assert resolved.items[0].reference_order_id == latest.pk


def test_price_check_ignores_other_currency_and_trucks(client, product, price):
    _shipped_before(client, product, 30, unit_price="3700", currency="KZT")
    _shipped_before(client, product, 20, unit_price="1.00", transport_type="truck")

    resolved = _resolve()

    assert resolved.ok, resolved.issues
    assert resolved.items[0].reference_price is None


def test_price_tolerance_comes_from_the_bot_settings(client, product, price):
    _shipped_before(client, product, 30, unit_price="7.00")
    assert _resolve().ok

    WhatsAppBotSettings.objects.create(price_tolerance_pct=5)

    assert issue_codes(_resolve()) == ["price_mismatch"]


def test_zero_tons_wagon_has_one_reason(client, product, price):
    resolved = _resolve(report(f"Д1с-{OWNER_WAGONS[0]}-0 тн"))

    assert issue_codes(resolved) == ["bad_tons"]


def test_tons_must_split_into_whole_bags(client, product, price):
    resolved = _resolve(report(f"Д1с-{OWNER_WAGONS[0]}-68,01 тн"))

    assert issue_codes(resolved) == ["bags_not_whole"]
    assert resolved.issues[0].subject == OWNER_WAGONS[0]


def test_report_from_the_future_needs_review(client, product, price):
    tomorrow = timezone.localdate() + timedelta(days=1)

    resolved = _resolve(report(f"Д1с-{OWNER_WAGONS[0]}-68 тн", header=_header(tomorrow)))

    assert issue_codes(resolved) == ["future_day"]


def test_parse_issues_are_kept_alongside_resolution(client, product, price):
    resolved = _resolve(report(f"Д1с-{OWNER_WAGONS[0]}-68 тн", "Итого 68 т"))

    assert "unknown_line" in issue_codes(resolved)
    assert not resolved.ok


def test_short_stock_is_a_warning_not_a_blocker(client, product, price, boss):
    StockItem.objects.filter(product=product).update(bags=100)

    resolved = _resolve()

    assert resolved.ok
    assert issue_codes(resolved, "warnings") == ["stock_short"]
    assert "100" in resolved.warnings[0].message


def _shipped_wagon(client, product, *, day, number=OWNER_WAGONS[3]):
    return train_order(client, product, shipped_at=backdate_moment(day), wagons=[number])


@pytest.mark.parametrize(
    ("days_from_report", "duplicate"),
    [(0, True), (-3, True), (3, True), (-4, False), (4, False), (-10, False), (-20, False)],
)
def test_wagon_is_a_duplicate_only_within_three_days_of_the_report_date(
    client, product, price, days_from_report, duplicate,
):
    """Повторно присланный отчёт — той же датой; раньше чем через 3 дня вагон под погрузку не вернётся."""
    earlier = _shipped_wagon(client, product, day=OWNER_DAY + timedelta(days=days_from_report))

    resolved = _resolve()

    assert issue_codes(resolved) == (["wagon_already_shipped"] if duplicate else [])
    if duplicate:
        assert resolved.issues[0].order_id == earlier.pk
        assert resolved.issues[0].subject == OWNER_WAGONS[3]


def test_duplicate_window_comes_from_the_bot_settings(client, product, price):
    WhatsAppBotSettings.objects.create(duplicate_window_days=10)
    _shipped_wagon(client, product, day=OWNER_DAY - timedelta(days=10))

    assert issue_codes(_resolve()) == ["wagon_already_shipped"]
    WhatsAppBotSettings.objects.update(duplicate_window_days=3)
    assert _resolve().ok


def test_duplicate_window_without_the_settings_row_is_three_days(client, product, price):
    _shipped_wagon(client, product, day=OWNER_DAY - timedelta(days=4))

    assert _resolve().ok
    # Чтение настройки строку не создаёт: на проде её может ещё не быть.
    assert not WhatsAppBotSettings.objects.exists()
    assert WhatsAppBotSettings.load().duplicate_window_days == 3


@pytest.mark.parametrize(("stored", "migrated"), [(14, 3), (7, 7)])
def test_old_default_window_moves_to_three_days(stored, migrated):
    """Старое «14 дней» (отклонено владельцем) становится ±3; своё значение администратора остаётся."""
    migration = importlib.import_module("apps.bots.migrations.0005_whatsapp_bot_duplicate_window_3_days")
    WhatsAppBotSettings.objects.create(duplicate_window_days=stored)

    migration.forwards(django_apps, None)

    assert WhatsAppBotSettings.load().duplicate_window_days == migrated


def test_wagon_of_a_deleted_order_is_not_a_duplicate(client, product, price):
    earlier = _shipped_wagon(client, product, day=OWNER_DAY - timedelta(days=3), number=OWNER_WAGONS[0])
    Order.all_objects.filter(pk=earlier.pk).update(deleted_at=timezone.now())

    assert _resolve().ok


@pytest.mark.parametrize(
    ("fields", "duplicate"),
    [
        ({"arrival_date": OWNER_DAY + timedelta(days=2)}, True),
        ({"arrival_date": OWNER_DAY - timedelta(days=1), "status": "confirmed"}, True),
        ({"arrival_date": OWNER_DAY + timedelta(days=3)}, False),
        ({"arrival_date": OWNER_DAY, "bags": OWNER_BAGS - 1360}, False),
        ({"arrival_date": OWNER_DAY, "status": "cancelled"}, False),
        ({"arrival_date": OWNER_DAY, "transport_type": "truck"}, False),
    ],
)
def test_manual_wagon_order_for_the_same_bags_is_a_duplicate(client, product, price, fields, duplicate):
    manual = train_order(client, product, **fields)

    resolved = _resolve()

    assert issue_codes(resolved) == (["manual_order_duplicate"] if duplicate else [])
    if duplicate:
        assert resolved.issues[0].order_id == manual.pk


@pytest.mark.parametrize("dated_by", ["created_at", "shipped_at"])
def test_manual_wagon_order_without_arrival_date_is_found_by_its_dates(client, product, price, dated_by):
    """Динара не всегда ставит дату прибытия: ручной заказ опознаётся и по созданию, и по отгрузке."""
    far = backdate_moment(OWNER_DAY - timedelta(days=30))
    near = backdate_moment(OWNER_DAY + timedelta(days=1))
    manual = train_order(client, product, shipped_at=near if dated_by == "shipped_at" else far)
    Order.objects.filter(pk=manual.pk).update(created_at=near if dated_by == "created_at" else far)

    resolved = _resolve()

    assert issue_codes(resolved) == ["manual_order_duplicate"]
    assert resolved.issues[0].order_id == manual.pk


def test_manual_duplicate_check_skips_orders_shipped_by_report(client, product, price, conductor):
    """Одинаковые партии идут каждый день: заказ, проведённый по отчёту, — не ручной дубль."""
    yesterday = OWNER_DAY - timedelta(days=1)
    other_wagons = ("28087773", "28087781", "28087799", "28087807", "28087815", "28087823",
                    "28087831", "28087849", "28087856", "28087864", "28087872", "28087880")
    conduct_rail_report(
        parse_rail_report(report(*(f"Д1с-{n}-68 тн" for n in other_wagons), header=_header(yesterday))),
        conductor,
    )

    assert _resolve().ok


# --- conduct_rail_report -------------------------------------------------------------------------


def _local_date(value):
    return timezone.localtime(value).date()


def test_owner_report_is_conducted_into_a_shipped_wagon_order(client, product, price, conductor):
    order = conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)

    assert order.status == "shipped"
    assert (order.client, order.currency, order.department, order.transport_type) == (
        client, "USD", "export", "train")
    assert order.rail_station == "Раустан"
    assert order.truck_number == ""
    assert order.arrival_date == OWNER_DAY
    assert order.created_by == conductor
    (item,) = order.items.all()
    assert (item.product, item.quantity, item.unit_price) == (product, OWNER_BAGS, Decimal("7.50"))
    assert order.total_amount == Decimal("122400.00")
    assert stock_bags(product) == 20000 - OWNER_BAGS
    wagons = list(order.shipment.wagons.all())
    assert [wagon.number for wagon in wagons] == list(OWNER_WAGONS)
    assert {(wagon.bags, wagon.weight_kg) for wagon in wagons} == {(1360, Decimal("68000.00"))}
    assert order.shipment.bags_loaded == OWNER_BAGS
    # Отчёт за 19.09: заказ, отгрузка и долг — в тот день (как фиксация задним
    # числом), подтверждение и запись об отчёте — сегодня (аудит).
    assert order.created_at == backdate_moment(OWNER_DAY)
    assert _local_date(order.shipment.shipped_at) == OWNER_DAY
    debt = EventLog.objects.get(order=order, event_type="debt")
    assert (debt.payload["amount"], _local_date(debt.created_at)) == ("122400.00", OWNER_DAY)
    assert _local_date(EventLog.objects.get(order=order, event_type="shipment").created_at) == OWNER_DAY
    confirmed = EventLog.objects.get(order=order, event_type="status", payload__to="confirmed")
    assert _local_date(confirmed.created_at) == timezone.localdate()
    audit = EventLog.objects.get(order=order, event_type="rail_report")
    assert audit.message == (
        "Заказ по отчёту о вагонах: ООО OSIYO NAV NIHOL, 12 ваг., ст. Раустан, 816 т, 16320 меш.")
    assert audit.payload["wagons"] == list(OWNER_WAGONS)
    assert audit.payload["day"] == "2026-09-19"
    assert _local_date(audit.created_at) == timezone.localdate()
    # Цены клиента бот не трогает.
    assert ClientPrice.objects.get(client=client, product=product).price == Decimal("7.50")


def test_todays_report_keeps_the_real_creation_time(client, product, price, conductor):
    before = timezone.now()

    order = conduct_rail_report(parse_rail_report(report(
        *(f"Д1с-{number}-68 тн" for number in OWNER_WAGONS), header=_header(timezone.localdate()))), conductor)

    assert order.created_at >= before
    assert order.shipment.shipped_at >= before


def test_report_in_the_profile_currency_is_conducted_in_it(client, product, conductor, boss):
    """Профиль «ООО OSIYO → KZT»: цена, сумма заказа и долг — в тенге, прайс USD не трогается."""
    remember_client_profile("ООО OSIYO NAV NIHOL", client, "KZT", boss)
    ClientPrice.objects.create(client=client, product=product, currency="KZT", price="3700")

    order = conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)

    assert (order.status, order.currency) == ("shipped", "KZT")
    assert order.items.get().unit_price == Decimal("3700.00")
    assert order.total_amount == Decimal("60384000.00")
    assert EventLog.objects.get(order=order, event_type="debt").payload["amount"] == "60384000.00"
    assert not ClientPrice.objects.filter(client=client, currency="USD").exists()


@pytest.mark.parametrize("tons", ["680", "100000"])
def test_tonnage_typo_goes_to_review_without_touching_stock(client, product, price, conductor, tons):
    """«680 тн» — не 13 600 мешков в долг, а «на разбор»; 100 000 т — не падение базы."""
    movements = StockMovement.objects.count()
    lines = [f"Д1с-{number}-68 тн" for number in OWNER_WAGONS]
    lines[0] = f"Д1с-{OWNER_WAGONS[0]}-{tons} тн"

    with pytest.raises(ValidationError) as caught:
        conduct_rail_report(parse_rail_report(report(*lines)), conductor)

    assert caught.value.detail["code"] == "rail_report_needs_review"
    assert caught.value.detail["issues"] == ["bad_tons"]
    assert not Order.objects.exists()
    assert StockMovement.objects.count() == movements


def test_client_hears_about_the_wagons_once(client, product, price, conductor):
    order = conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)

    assert list(Notification.objects.filter(client=client).values_list("text", flat=True)) == [
        f"Заказ №{order.pk} отгружен (12 ваг., ст. Раустан)"
    ]


def test_conducting_keeps_the_client_price_list_as_is(client, product, price, user_with_perms):
    """Цена заказа — из прайса клиента; проводящий с правом менять цены прайс не переписывает."""
    pricer = user_with_perms("rail-pricer", codes=[*CONDUCT_CODES, "clients.set_price"])

    conduct_rail_report(parse_rail_report(OWNER_REPORT), pricer)

    price.refresh_from_db()
    assert (price.price, price.updated_by) == (Decimal("7.50"), None)


def test_short_stock_does_not_stop_a_wagon_that_already_left(client, product, price, conductor):
    StockItem.objects.filter(product=product).update(bags=100)

    order = conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)

    assert order.status == "shipped"
    assert stock_bags(product) == 100 - OWNER_BAGS


def test_employee_of_another_department_cannot_conduct(client, product, price, conductor):
    move_to_retail(conductor)

    with pytest.raises(PermissionDenied) as caught:
        conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)

    assert "другим отделом" in str(caught.value.detail)
    assert not Order.objects.exists()
    assert stock_bags(product) == 20000


def test_report_needing_review_creates_nothing(client, price, conductor):
    ProductAlias.objects.all().delete()
    movements = StockMovement.objects.count()

    with pytest.raises(ValidationError) as caught:
        conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)

    assert caught.value.detail["code"] == "rail_report_needs_review"
    assert "Д1с" in str(caught.value.detail["detail"])
    assert not Order.objects.exists()
    assert not ShipmentWagon.objects.exists()
    assert StockMovement.objects.count() == movements


def test_same_report_twice_is_refused_as_duplicate(client, product, price, conductor):
    conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)

    with pytest.raises(ValidationError) as caught:
        conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)

    assert caught.value.detail["code"] == "rail_report_needs_review"
    assert Order.objects.count() == 1
    assert stock_bags(product) == 20000 - OWNER_BAGS


@pytest.mark.parametrize("missing", ["orders.create", "orders.confirm", "loader.confirm", "loader.wagons"])
def test_conducting_needs_order_and_wagon_rights(client, product, price, user_with_perms, missing):
    user = user_with_perms("rail-limited", codes=[code for code in CONDUCT_CODES if code != missing])

    with pytest.raises(PermissionDenied):
        conduct_rail_report(parse_rail_report(OWNER_REPORT), user)

    assert not Order.objects.exists()
    assert stock_bags(product) == 20000


def test_trucks_loader_cannot_conduct_wagons(client, product, price, user_with_perms):
    user = user_with_perms(
        "rail-trucks", codes=["orders.create", "orders.confirm", "loader.confirm", "loader.trucks"])

    with pytest.raises(PermissionDenied):
        conduct_rail_report(parse_rail_report(OWNER_REPORT), user)

    assert not Order.objects.exists()
