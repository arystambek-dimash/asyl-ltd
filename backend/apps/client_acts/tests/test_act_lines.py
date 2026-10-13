"""Строка акта сверки в заказе — документ 1С: без цвета, фасовки и мешков везде, где видна позиция."""

import importlib
from decimal import Decimal

import pytest
from django.apps import apps as django_apps

from apps.bots.truck_report import compose_truck_report
from apps.catalog.models import Product
from apps.client_acts.models import ClientActDocument
from apps.client_acts.parser import parse_act
from apps.client_acts.services import ACT_PRODUCT, import_act_chunk
from apps.clients.models import Client
from apps.clients.reports.statements import build_statement
from apps.clients.reports.statements.presentation import operation_display
from apps.clients.services import client_history
from apps.orders.models import Order, OrderItem
from apps.orders.services import replace_items
from apps.shipments.serializers import LoaderOrderItemSerializer, LoaderOrderSerializer
from apps.shipments.waybill import build_waybill_pdf

from .act_factory import act_xlsx, sale

pytestmark = pytest.mark.django_db

IMPORT_CODES = ["orders.view", "orders.edit", "orders.confirm", "payments.view", "payments.create", "reports.view"]
DOCUMENT = "Реализация ТМЗ и услуг 527 от 10.06.2026"


@pytest.fixture
def importer(user_with_perms, departments):
    return user_with_perms("act-importer", codes=IMPORT_CODES, department=departments[0])


@pytest.fixture
def client(departments):
    return Client.objects.create_with_user(
        first_name="Дан Агро Групп", phone="+996 555 000 000", department=departments[0], currency="USD",
    )


@pytest.fixture
def act_order(client, importer):
    act = parse_act(act_xlsx([sale("10.06.2026", 6625, "527")]))
    assert import_act_chunk(client, act, importer)["finished"]
    return Order.objects.get(client=client)


def test_act_line_is_the_1c_document_without_colour_weight_or_bags(act_order):
    item = act_order.items.get()

    assert (item.product_label, item.act_document, item.is_act_line) == (ACT_PRODUCT["name"], DOCUMENT, True)
    assert item.line_label == f"{ACT_PRODUCT['name']}: {DOCUMENT}"
    assert item.product_plain_label == ACT_PRODUCT["name"]
    assert (act_order.goods_bags, act_order.ordered_bags) == (0, 1)


def test_order_and_loader_api_show_the_document_instead_of_bags(api_as, importer, act_order):
    item = api_as(importer).get(f"/api/orders/{act_order.pk}/").data["items"][0]

    assert (item["product_label"], item["act_document"], item["weight_kg"]) == (ACT_PRODUCT["name"], DOCUMENT, None)
    loader = LoaderOrderItemSerializer(act_order.items.get()).data
    assert (loader["label"], loader["act_document"]) == (ACT_PRODUCT["name"], DOCUMENT)


def test_client_card_rows_have_the_document_and_no_bags(client, act_order):
    history = client_history(client)

    assert history["sales"][0]["items"] == [
        {"label": ACT_PRODUCT["name"], "qty": 1, "is_bonus": False, "act_document": DOCUMENT},
    ]
    assert history["sales"][0]["bags"] == 0
    assert history["debts"][0]["bags"] == 0


def test_statements_and_waybill_render_act_lines(client, act_order):
    class Sale:
        kind = "sale"
        order = Order.objects.prefetch_related("items").get(pk=act_order.pk)

    assert operation_display(Sale).description == f"{ACT_PRODUCT['name']}: {DOCUMENT}"
    assert build_statement("xlsx", client=client)[:2] == b"PK"
    assert build_statement("pdf", client=client)[:4] == b"%PDF"
    assert build_waybill_pdf(act_order)[:4] == b"%PDF"


def test_truck_report_skips_orders_made_only_of_act_lines(act_order):
    report = compose_truck_report([Order.objects.prefetch_related("items").get(pk=act_order.pk)])

    assert (report.text, report.order_ids) == ("", [])


def test_loader_card_has_no_bags_or_weight_for_an_act_order(act_order):
    order = Order.objects.prefetch_related("items").get(pk=act_order.pk)
    card = LoaderOrderSerializer()

    assert (card.get_bags(order), card.get_total_kg(order)) == (0, "0.00")


def test_editing_an_act_order_keeps_the_line_a_1c_document(importer, act_order):
    item = act_order.items.get()

    replace_items(
        act_order, [{"product": item.product, "quantity": 1}], {str(item.product_id): "7000"}, importer,
        edit_reason="Исправили сумму документа",
    )

    edited = act_order.items.get()
    assert (edited.act_document, edited.product_label, edited.unit_price) == (DOCUMENT, ACT_PRODUCT["name"], 7000)


def test_migration_shows_already_imported_act_lines_as_documents(act_order):
    migration = importlib.import_module("apps.client_acts.migrations.0002_act_items_show_document")
    # Как было до миграции: подпись с цветом и фасовкой, документа нет.
    OrderItem.objects.filter(order=act_order).update(
        product_label_snapshot=f"{ACT_PRODUCT['name']} · Синий 2 кг", act_document="",
    )
    other = Order.objects.create(client=act_order.client, status="shipped", currency="USD", department="mill")
    flour = Product.objects.create(name="Мука", color="Red", weight_kg=Decimal("50"))
    untouched = OrderItem.objects.create(order=other, product=flour, quantity=3, unit_price=Decimal("10"))

    migration.show_document(django_apps, None)

    item = act_order.items.get()
    assert (item.product_label_snapshot, item.act_document) == (ACT_PRODUCT["name"], DOCUMENT)
    untouched.refresh_from_db()
    assert untouched.act_document == ""
    assert ClientActDocument.objects.filter(order=act_order).exists()
