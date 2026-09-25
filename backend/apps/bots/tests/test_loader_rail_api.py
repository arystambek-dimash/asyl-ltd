"""«Вставить отчёт» у грузчика: предпросмотр без записи, разрешение неизвестного, «Провести»,
«Отгрузить по отчёту» и вагоны в истории, поиске и списке заказов."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.bots.models import BotClientProfile, WhatsAppBotSettings
from apps.bots.tests.samples import (
    CONDUCT_CODES,
    OWNER_BAGS,
    OWNER_DAY,
    OWNER_REPORT,
    OWNER_WAGONS,
    manual_train_order,
    move_to_retail,
    report,
    stock_bags,
    train_order,
)
from apps.catalog.models import ClientPrice, Product, ProductAlias
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.backdate import backdate_moment
from apps.orders.models import Order

pytestmark = pytest.mark.django_db

PREVIEW = "/api/loader/rail-report/preview/"
APPLY = "/api/loader/rail-report/apply/"
PRODUCT_CODES = "/api/loader/rail-report/product-codes/"
CLIENT_NAMES = "/api/loader/rail-report/client-names/"
OPTIONS = "/api/loader/rail-report/options/"


def _post(api, url, **body):
    return api.post(url, {"text": OWNER_REPORT, **body}, format="json")


def _report_order(client, product, numbers, *, day=None):
    """Отгруженный по отчёту вагонный заказ — без проведения, для списков."""
    shipped_at = backdate_moment(day or timezone.localdate())
    return train_order(client, product, shipped_at=shipped_at, wagons=numbers, rail_station="Раустан")


# --- предпросмотр ---------------------------------------------------------------------------------


def test_preview_shows_what_will_be_conducted_and_writes_nothing(auth_client, client, product, price, conductor):
    aliases = ProductAlias.objects.count()

    response = _post(auth_client(conductor), PREVIEW)

    assert response.status_code == 200, response.data
    data = response.data
    assert (data["ok"], data["can_apply"], data["order_id"]) == (True, True, None)
    assert (data["day"], data["country"], data["station"], data["declared_wagons"]) == (
        "2026-09-19", "Узбекистан", "Раустан", 12)
    assert data["client"] == {"id": client.pk, "name": "ООО OSIYO NAV NIHOL", "profile": False}
    assert data["currency"] == "USD"
    assert [wagon["number"] for wagon in data["wagons"]] == list(OWNER_WAGONS)
    assert data["wagons"][0] == {
        "position": 1, "line": 3, "number": OWNER_WAGONS[0], "number_status": "ok", "code": "Д1с",
        "product_id": product.pk, "product_label": str(product), "tons": "68", "bags": 1360,
        "unit_price": "7.50", "amount": "10200.00",
    }
    assert data["items"] == [{
        "product_id": product.pk, "product_label": str(product), "code": "Д1с", "wagons": 12, "bags": OWNER_BAGS,
        "unit_price": "7.50", "amount": "122400.00", "reference_price": None, "reference_order_id": None,
    }]
    assert data["totals"] == {"wagons": 12, "tons": "816", "bags": OWNER_BAGS, "amount": "122400.00", "currency": "USD"}
    assert (data["issues"], data["unresolved"]) == ([], {"client": "", "products": []})
    assert not Order.objects.exists()
    assert ProductAlias.objects.count() == aliases
    assert stock_bags(product) == 20000


def test_preview_marks_a_wagon_number_with_a_wrong_check_digit(auth_client, client, product, price, conductor):
    text = report(*(f"Д1с-{number}-68 тн" for number in OWNER_WAGONS[:11]), "Д1с-28087766-68 тн")

    data = auth_client(conductor).post(PREVIEW, {"text": text}, format="json").data

    assert data["wagons"][-1]["number_status"] == "check_digit"
    (issue,) = data["issues"]
    assert (issue["code"], issue["line"], issue["subject"]) == ("wagon_check_digit", 14, "28087766")
    assert (data["ok"], data["can_apply"]) == (False, False)


def test_preview_total_waits_for_every_wagon(auth_client, client, product, price, conductor):
    text = report(*(f"Д1с-{number}-68 тн" for number in OWNER_WAGONS[:2]), f"Б2-{OWNER_WAGONS[2]}-68 тн")

    data = auth_client(conductor).post(PREVIEW, {"text": text}, format="json").data

    # Два вагона из трёх распознаны: частичная сумма выглядела бы итогом отчёта.
    assert (data["totals"]["tons"], data["totals"]["bags"], data["totals"]["amount"]) == ("204", 2720, None)
    assert data["unresolved"]["products"] == ["Б2"]


def test_empty_and_overlong_text_is_an_input_error(auth_client, conductor):
    api = auth_client(conductor)

    assert api.post(PREVIEW, {"text": "  "}, format="json").data["detail"]["text"] == ["Вставьте текст отчёта"]
    assert api.post(PREVIEW, {"text": "Д" * 8193}, format="json").status_code == 400


def test_report_sheet_is_the_wagons_tab(auth_client, client, product, price, user_with_perms):
    api = auth_client(user_with_perms(
        "trucks", codes=["loader.view", "loader.confirm", "loader.trucks", "orders.create", "orders.confirm"]))

    assert _post(api, PREVIEW).status_code == 403
    assert api.get(OPTIONS).status_code == 403
    assert _post(api, APPLY).status_code == 403
    assert not Order.objects.exists()


def test_report_sheet_needs_the_loader_page(auth_client, client, product, price, user_with_perms):
    api = auth_client(user_with_perms("no-page", codes=["loader.wagons", "orders.create", "orders.confirm"]))

    assert _post(api, PREVIEW).status_code == 403
    assert api.get(OPTIONS).status_code == 403


# --- разрешение неизвестного --------------------------------------------------------------------


def test_unknown_product_code_is_remembered_from_the_sheet(auth_client, client, product, price, conductor):
    ProductAlias.objects.all().delete()
    api = auth_client(conductor)

    data = _post(api, PREVIEW).data
    assert data["unresolved"]["products"] == ["Д1с"]
    assert data["wagons"][0]["product_id"] is None
    assert (data["can_remember_products"], data["can_apply"]) == (True, False)
    assert {"id": product.pk, "label": str(product), "weight_kg": "50.00"} in api.get(OPTIONS).data["products"]

    response = _post(api, PRODUCT_CODES, code="Д1с", product=product.pk)

    assert response.status_code == 200, response.data
    assert (response.data["ok"], response.data["can_apply"]) == (True, True)
    alias = ProductAlias.objects.get()
    assert (alias.code, alias.spelling, alias.product, alias.created_by) == ("Д1C", "Д1с", product, conductor)


def test_sheet_does_not_take_a_code_from_another_live_product(auth_client, client, product, price, conductor):
    other = Product.objects.create(name="Мука первый сорт", color="Red", weight_kg="50")

    response = _post(auth_client(conductor), PRODUCT_CODES, code="Д1с", product=other.pk)

    # Бот списывает склад по словарю: перенести код — только на странице «Товары».
    assert response.status_code == 400
    assert response.data["code"] == "alias_taken"
    assert ProductAlias.objects.get().product == product


def test_sheet_gives_the_code_of_an_archived_product_to_the_picked_one(
    auth_client, client, product, price, conductor,
):
    Product.objects.filter(pk=product.pk).update(is_active=False)
    other = Product.objects.create(name="Мука первый сорт", color="Red", weight_kg="50")
    api = auth_client(conductor)
    assert _post(api, PREVIEW).data["unresolved"]["products"] == ["Д1с"]

    response = _post(api, PRODUCT_CODES, code="Д1с", product=other.pk)

    assert response.status_code == 200, response.data
    assert response.data["unresolved"]["products"] == []
    assert ProductAlias.objects.get().product == other


def test_unknown_client_is_remembered_with_its_currency(auth_client, client, product, conductor):
    ClientPrice.objects.create(client=client, product=product, currency="KZT", price="3500")
    text = OWNER_REPORT.replace("ООО OSIYO NAV NIHOL", "OSIYO Ташкент")
    api = auth_client(conductor)

    data = api.post(PREVIEW, {"text": text}, format="json").data
    assert data["unresolved"]["client"] == "OSIYO Ташкент"
    assert data["client"] is None
    assert {"id": client.pk, "name": "ООО OSIYO NAV NIHOL", "currency": "USD", "department_name": "Экспорт"} in (
        api.get(OPTIONS).data["clients"])

    response = api.post(
        CLIENT_NAMES, {"text": text, "client_name": "OSIYO Ташкент", "client": client.pk, "currency": "KZT"},
        format="json")

    assert response.status_code == 200, response.data
    assert (response.data["ok"], response.data["currency"]) == (True, "KZT")
    assert response.data["client"] == {"id": client.pk, "name": "ООО OSIYO NAV NIHOL", "profile": True}
    assert response.data["totals"]["amount"] == "57120000.00"
    profile = BotClientProfile.objects.get()
    assert (profile.name, profile.client, profile.currency, profile.created_by) == (
        "OSIYO Ташкент", client, "KZT", conductor)
    # Бот считает по профилю деньги — изменение видно в журнале.
    assert EventLog.objects.filter(event_type="clients", user=conductor, payload__currency="KZT").exists()


def test_client_of_another_department_cannot_be_picked(auth_client, client, product, price, conductor):
    move_to_retail(conductor)
    api = auth_client(conductor)

    response = _post(api, CLIENT_NAMES, client_name="OSIYO", client=client.pk, currency="USD")

    assert response.status_code == 400
    assert "client" in response.data["detail"]
    assert not BotClientProfile.objects.exists()
    assert api.get(OPTIONS).data["clients"] == []


def test_name_of_another_departments_client_cannot_be_repointed(auth_client, client, product, price, conductor, boss):
    own = Client.objects.create_with_user(first_name="Свой", phone="+7 700 000 00 01", department=move_to_retail(conductor))
    profile = BotClientProfile.objects.create(name="OSIYO", client=client, currency="USD", created_by=boss)
    text = OWNER_REPORT.replace("ООО OSIYO NAV NIHOL", "OSIYO")
    api = auth_client(conductor)
    assert api.post(PREVIEW, {"text": text}, format="json").data["can_remember_clients"] is False

    response = api.post(
        CLIENT_NAMES, {"text": text, "client_name": "OSIYO", "client": own.pk, "currency": "KZT"}, format="json")

    # Иначе все отчёты «OSIYO» (и бота) списали бы склад и долг на клиента розницы.
    assert response.status_code == 403
    profile.refresh_from_db()
    assert (profile.client, profile.currency) == (client, "USD")
    # Название из карточки клиента другого отдела — тоже его.
    by_card = _post(api, CLIENT_NAMES, client_name="ООО OSIYO NAV NIHOL", client=own.pk, currency="KZT")
    assert by_card.status_code == 403
    assert BotClientProfile.objects.count() == 1


def test_preview_of_another_departments_client_hides_prices_and_orders(
    auth_client, client, product, price, conductor,
):
    manual_train_order(client, product, arrival_date=OWNER_DAY)
    move_to_retail(conductor)

    data = _post(auth_client(conductor), PREVIEW).data

    assert [issue["code"] for issue in data["issues"]] == ["client_other_department"]
    assert (data["ok"], data["can_apply"], data["can_remember_clients"]) == (False, False, False)
    assert {(wagon["unit_price"], wagon["amount"]) for wagon in data["wagons"]} == {(None, None)}
    assert (data["items"][0]["unit_price"], data["totals"]["amount"], data["shippable_orders"]) == (None, None, [])


def test_only_a_waiting_manual_duplicate_is_offered_for_shipping(auth_client, client, product, price, conductor):
    waiting = manual_train_order(client, product, arrival_date=OWNER_DAY)
    shipped = manual_train_order(client, product, arrival_date=OWNER_DAY)
    Order.objects.filter(pk=shipped.pk).update(status="shipped")

    data = _post(auth_client(conductor), PREVIEW).data

    duplicates = {issue["order_id"] for issue in data["issues"] if issue["code"] == "manual_order_duplicate"}
    assert duplicates == {waiting.pk, shipped.pk}
    # Отгруженному вручную «Отгрузить по отчёту» нечего — только предупреждение.
    assert data["shippable_orders"] == [waiting.pk]


@pytest.mark.parametrize(("setting", "duplicate"), [(None, False), (10, True)])
def test_loader_uses_the_bot_duplicate_window(auth_client, client, product, price, conductor, setting, duplicate):
    """Дубль вагона — в живом заказе с датой ±N дней от даты отчёта; N — из настроек бота (без строки — 3)."""
    if setting is not None:
        WhatsAppBotSettings.objects.create(duplicate_window_days=setting)
    earlier = _report_order(client, product, [OWNER_WAGONS[0]], day=OWNER_DAY - timedelta(days=10))

    data = _post(auth_client(conductor), PREVIEW).data
    apply = _post(auth_client(conductor), APPLY)

    duplicates = [issue["order_id"] for issue in data["issues"] if issue["code"] == "wagon_already_shipped"]
    assert duplicates == ([earlier.pk] if duplicate else [])
    assert apply.status_code == (400 if duplicate else 200), apply.data


@pytest.mark.parametrize(("tolerance", "mismatch"), [(None, False), ("5", True), ("30", False)])
def test_loader_uses_the_bot_price_tolerance(auth_client, client, product, price, conductor, tolerance, mismatch):
    """Допуск цены — из настроек бота, как у бота и в журнале (без строки — 15%)."""
    if tolerance is not None:
        WhatsAppBotSettings.objects.create(price_tolerance_pct=tolerance)
    earlier = _report_order(client, product, [OWNER_WAGONS[0]], day=OWNER_DAY - timedelta(days=30))
    # 7,50 против 7,00 в прошлом вагонном заказе — разница 7%.
    earlier.items.update(unit_price="7.00")

    data = _post(auth_client(conductor), PREVIEW).data
    apply = _post(auth_client(conductor), APPLY)

    mismatches = [issue["order_id"] for issue in data["issues"] if issue["code"] == "price_mismatch"]
    assert mismatches == ([earlier.pk] if mismatch else [])
    assert apply.status_code == (400 if mismatch else 200), apply.data


def test_wagon_loader_sees_the_report_but_cannot_teach_the_dictionary(
    auth_client, client, product, price, wagon_loader,
):
    ProductAlias.objects.all().delete()
    api = auth_client(wagon_loader)

    data = _post(api, PREVIEW).data
    assert (data["can_apply"], data["can_remember_products"], data["can_remember_clients"]) == (False, False, False)
    assert api.get(OPTIONS).data == {"products": [], "clients": []}

    assert _post(api, PRODUCT_CODES, code="Д1с", product=product.pk).status_code == 403
    assert _post(api, CLIENT_NAMES, client_name="OSIYO", client=client.pk, currency="USD").status_code == 403
    assert not ProductAlias.objects.exists()
    assert not BotClientProfile.objects.exists()


# --- «Провести» --------------------------------------------------------------------------------------


def test_apply_conducts_the_report_and_answers_with_the_history_row(auth_client, client, product, price, conductor):
    response = _post(auth_client(conductor), APPLY)

    assert response.status_code == 200, response.data
    order = Order.objects.get()
    row = response.data
    assert (row["id"], row["status"], row["transport_type"], row["rail_station"]) == (
        order.pk, "shipped", "train", "Раустан")
    assert [wagon["number"] for wagon in row["wagons"]] == list(OWNER_WAGONS)
    assert row["wagons"][0] == {
        "number": OWNER_WAGONS[0], "product_label": str(product), "bags": 1360, "weight_kg": "68000.00"}
    assert row["bags"] == OWNER_BAGS
    assert (row["report_sent_at"], row["report_status"]) == (None, "")
    assert stock_bags(product) == 20000 - OWNER_BAGS


def test_apply_of_a_report_needing_review_writes_nothing(auth_client, client, price, conductor):
    ProductAlias.objects.all().delete()

    response = _post(auth_client(conductor), APPLY)

    assert response.status_code == 400
    assert response.data["code"] == "rail_report_needs_review"
    assert not Order.objects.exists()


# Весь набор прав проведения — test_rail; здесь — отказ сервиса и право кнопки «Провести».
@pytest.mark.parametrize("missing", ["orders.create", "loader.confirm"])
def test_apply_needs_order_and_shipping_rights(auth_client, client, product, price, user_with_perms, missing):
    user = user_with_perms("limited", codes=[code for code in CONDUCT_CODES if code != missing])

    assert _post(auth_client(user), APPLY).status_code == 403
    assert not Order.objects.exists()


# --- «Отгрузить по отчёту» ----------------------------------------------------------------------


def test_waiting_wagon_order_ships_by_report_without_order_rights(auth_client, client, product, wagon_loader):
    order = manual_train_order(client, product)
    api = auth_client(wagon_loader)

    preview = _post(api, PREVIEW, order=order.pk).data
    assert (preview["order_id"], preview["ok"], preview["can_apply"]) == (order.pk, True, True)
    assert preview["items"][0]["unit_price"] == "7.40"

    response = _post(api, APPLY, order=order.pk)

    assert response.status_code == 200, response.data
    assert (response.data["id"], response.data["status"]) == (order.pk, "shipped")
    assert len(response.data["wagons"]) == 12
    assert Order.objects.count() == 1


def test_order_report_with_other_bags_is_shown_and_refused(auth_client, client, product, wagon_loader):
    order = manual_train_order(client, product, bags=4080)
    api = auth_client(wagon_loader)

    preview = _post(api, PREVIEW, order=order.pk).data
    assert [issue["code"] for issue in preview["issues"]] == ["rail_bags_mismatch"]
    assert _post(api, APPLY, order=order.pk).status_code == 400
    order.refresh_from_db()
    assert order.status == "confirmed"


def test_order_of_another_department_is_not_found(auth_client, client, product, wagon_loader):
    move_to_retail(wagon_loader)
    order = manual_train_order(client, product)

    assert _post(auth_client(wagon_loader), PREVIEW, order=order.pk).status_code == 404


# --- вагоны в истории, поиске и заказах ---------------------------------------------------------


def test_history_finds_a_report_order_by_any_wagon_number(auth_client, client, product, conductor):
    order = _report_order(client, product, OWNER_WAGONS[:3])
    _report_order(client, product, OWNER_WAGONS[3:5])
    api = auth_client(conductor)

    for search in (OWNER_WAGONS[2], "2808 7674", OWNER_WAGONS[2][-4:]):
        rows = api.get("/api/loader/history/", {"transport": "train", "search": search}).data
        assert [row["id"] for row in rows] == [order.pk], search


def test_history_lists_wagons_without_n_plus_one(
    auth_client, client, product, conductor, django_assert_max_num_queries,
):
    # Вчерашние отгрузки: у свежей (≤ часа, а до полудня — «из будущего»)
    # «Отменить» спрашивает журнал, и счёт запросов зависел бы от часа прогона.
    yesterday = timezone.localdate() - timedelta(days=1)
    url = f"/api/loader/history/?transport=train&date_from={yesterday.isoformat()}"
    api = auth_client(conductor)
    _report_order(client, product, OWNER_WAGONS[:2], day=yesterday)
    with CaptureQueriesContext(connection) as small:
        assert len(api.get(url).data) == 1
    for start in range(2, 12, 2):
        _report_order(client, product, OWNER_WAGONS[start:start + 2], day=yesterday)

    with django_assert_max_num_queries(len(small.captured_queries)):
        rows = api.get(url).data

    assert len(rows) == 6
    assert all(len(row["wagons"]) == 2 for row in rows)


def test_orders_api_shows_wagons_and_finds_by_wagon_once(
    auth_client, client, product, manager, django_assert_max_num_queries,
):
    order = _report_order(client, product, OWNER_WAGONS[:3])
    api = auth_client(manager)

    detail = api.get(f"/api/orders/{order.pk}/").data
    assert (detail["rail_station"], [wagon["number"] for wagon in detail["wagons"]]) == (
        "Раустан", list(OWNER_WAGONS[:3]))
    # Exists, а не JOIN: заказ с тремя вагонами не повторяется в списке.
    rows = api.get("/api/orders/", {"search": OWNER_WAGONS[1][:5]}).data
    assert [row["id"] for row in rows] == [order.pk]

    with CaptureQueriesContext(connection) as small:
        api.get("/api/orders/")
    for start in range(3, 12, 3):
        _report_order(client, product, OWNER_WAGONS[start:start + 3])
    with django_assert_max_num_queries(len(small.captured_queries)):
        api.get("/api/orders/")


def test_portal_shows_the_wagons_read_only(auth_client, make_user, product, department):
    user = make_user(username="portal-rail", client=True)
    portal_client = Client.objects.create_with_user(
        user=user, first_name="Портал", phone="+998 90 555 00 11", company_name="ООО Портал", department=department)
    order = _report_order(portal_client, product, OWNER_WAGONS[:2])
    api = auth_client(user)

    data = api.get(f"/api/portal/orders/{order.pk}/").data
    assert data["rail_station"] == "Раустан"
    assert [wagon["number"] for wagon in data["wagons"]] == list(OWNER_WAGONS[:2])

    api.patch(f"/api/portal/orders/{order.pk}/", {"rail_station": "Другая"}, format="json")
    order.refresh_from_db()
    assert order.rail_station == "Раустан"
    assert Decimal(data["wagons"][0]["weight_kg"]) == Decimal("68000")
