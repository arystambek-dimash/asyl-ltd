from io import BytesIO

import pytest
from openpyxl import load_workbook

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def test_statement_is_real_xlsx_with_financial_sheets(auth_client, user_with_perms):
    reporter = user_with_perms("statement", codes=["clients.view", "reports.view"])
    client = Client.objects.create(first_name="New", last_name="City", phone="1")
    product = Product.objects.create(name="Мука", color="Red", weight_kg="50")
    order = Order.objects.create(client=client, status="shipped", currency="USD")
    OrderItem.objects.create(order=order, product=product, quantity=10, unit_price="12.50")
    Payment.objects.create(
        order=order, amount="25", method="invoice", status="confirmed",
        confirmed_by=reporter,
    )

    response = auth_client(reporter).get(f"/api/clients/{client.pk}/statement/")

    assert response.status_code == 200
    assert response["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    wb = load_workbook(BytesIO(response.content), data_only=True)
    assert wb.sheetnames == ["Сводка", "Операции", "Заказы", "Позиции", "Платежи", "Долги"]
    assert wb["Операции"]["B4"].value == "Продажа / отгрузка"
    assert wb["Операции"]["F4"].value == "USD"
    assert wb["Операции"]["G4"].value == 125
    assert wb["Операции"]["B5"].value == "Оплата"
    assert wb["Операции"]["H5"].value == 25
    assert wb["Долги"]["G4"].value == 100
    assert EventLog.objects.filter(event_type="client_statement", user=reporter).exists()


def test_statement_requires_reports_permission(auth_client, user_with_perms):
    viewer = user_with_perms("statement-no", codes=["clients.view"])
    client = Client.objects.create(first_name="A", last_name="B", phone="2")
    assert auth_client(viewer).get(f"/api/clients/{client.pk}/statement/").status_code == 403
