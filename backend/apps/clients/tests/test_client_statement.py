from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import load_workbook

from apps.catalog.models import Product
from apps.clients.models import Client, Department
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem, Payment
from apps.shipments.models import Shipment

pytestmark = pytest.mark.django_db


def test_statement_is_real_xlsx_with_financial_sheets(auth_client, user_with_perms):
    reporter = user_with_perms("statement", codes=["clients.view", "reports.export"])
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


def test_statement_requires_export_permission(auth_client, user_with_perms):
    viewer = user_with_perms(
        "statement-no", codes=["clients.view", "reports.view"])
    client = Client.objects.create(first_name="A", last_name="B", phone="2")
    assert auth_client(viewer).get(f"/api/clients/{client.pk}/statement/").status_code == 403


def test_all_clients_statement_contains_detailed_cross_client_sheets(
    auth_client, user_with_perms,
):
    reporter = user_with_perms(
        "all-statements", codes=["clients.view", "reports.export"])
    first = Client.objects.create(first_name="New", last_name="City", phone="11")
    second = Client.objects.create(first_name="Old", last_name="Town", phone="22")
    product = Product.objects.create(name="Крупа", color="Blue", weight_kg="25")
    order = Order.objects.create(client=first, status="shipped", currency="KZT")
    OrderItem.objects.create(order=order, product=product, quantity=3, unit_price="100")
    Payment.objects.create(
        order=order, amount="100", method="cash", status="confirmed",
        confirmed_by=reporter,
    )

    response = auth_client(reporter).get("/api/clients/statement/")

    assert response.status_code == 200
    wb = load_workbook(BytesIO(response.content), data_only=True)
    assert wb.sheetnames == [
        "Сводка", "Клиенты", "Операции", "Заказы", "Позиции", "Платежи", "Долги",
    ]
    assert wb["Клиенты"]["B4"].value == first.name
    assert wb["Клиенты"]["B5"].value == second.name
    assert wb["Заказы"]["C4"].value == first.name
    assert wb["Позиции"]["E4"].value.startswith("Крупа")
    assert EventLog.objects.filter(event_type="clients_statement", user=reporter).exists()


def test_all_clients_statement_requires_export_permission(auth_client, user_with_perms):
    viewer = user_with_perms(
        "all-statements-no", codes=["clients.view", "reports.view"])
    assert auth_client(viewer).get("/api/clients/statement/").status_code == 403


def test_all_clients_statement_neutralizes_spreadsheet_formulas(
    auth_client, user_with_perms,
):
    reporter = user_with_perms(
        "formula-export", codes=["clients.view", "reports.export"])
    client = Client.objects.create(
        first_name='=HYPERLINK("https://example.invalid","open")',
        last_name="",
        phone="@SUM(1+1)",
    )
    product = Product.objects.create(
        name='=WEBSERVICE("http://127.0.0.1/internal")',
        color="Red",
        weight_kg="50",
    )
    order = Order.objects.create(
        client=client,
        status="shipped",
        notes="+cmd|' /C calc'!A0",
    )
    OrderItem.objects.create(
        order=order, product=product, quantity=1, unit_price="10")

    response = auth_client(reporter).get("/api/clients/statement/")

    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content), data_only=False)
    cells = [
        workbook["Клиенты"]["B4"],
        workbook["Клиенты"]["D4"],
        workbook["Позиции"]["E4"],
        workbook["Заказы"]["Q4"],
    ]
    assert all(cell.data_type == "s" for cell in cells)
    assert [cell.value[0] for cell in cells] == ["'", "'", "'", "'"]


def test_statement_period_matches_payment_recognition_day(auth_client, user_with_perms):
    """Оплата попадает в период по дню подтверждения — тому же, что показан в строке.

    Раньше фильтр шёл по paid_at, а в выписке печатался confirmed_at: деньги,
    подтверждённые в периоде, в выписку не попадали.
    """
    from datetime import timedelta
    from django.utils import timezone

    reporter = user_with_perms(
        "statement-period", codes=["clients.view", "reports.export"])
    client = Client.objects.create(first_name="Per", last_name="Iod", phone="9")
    product = Product.objects.create(name="Цемент", color="Grey", weight_kg="50")
    order = Order.objects.create(client=client, status="shipped")
    OrderItem.objects.create(
        order=order, product=product, quantity=1, unit_price="1000.00")

    long_ago = timezone.now() - timedelta(days=30)
    payment = Payment.objects.create(
        order=order, amount="400.00", method="cash", status="confirmed",
        confirmed_by=reporter,
    )
    # Записана 30 дней назад, подтверждена сегодня.
    Payment.objects.filter(pk=payment.pk).update(
        paid_at=long_ago, confirmed_at=timezone.now())

    today = timezone.localdate().isoformat()
    response = auth_client(reporter).get(
        f"/api/clients/{client.pk}/statement/?date_from={today}&date_to={today}")

    assert response.status_code == 200
    wb = load_workbook(BytesIO(response.content), data_only=True)
    paid_cell = wb["Сводка"]["D8"].value  # строка KZT: Заказов/Продажи/Оплачено
    assert paid_cell == 400, "оплата, подтверждённая сегодня, должна войти в период"


def test_statement_money_cells_keep_kopecks(auth_client, user_with_perms):
    """Крупные суммы не теряют копейки: в ячейку пишется Decimal, а не float."""
    reporter = user_with_perms(
        "statement-money", codes=["clients.view", "reports.export"])
    client = Client.objects.create(first_name="Big", last_name="Sum", phone="8")
    product = Product.objects.create(name="Щебень", color="Grey", weight_kg="50")
    order = Order.objects.create(client=client, status="shipped")
    OrderItem.objects.create(
        order=order, product=product, quantity=1, unit_price="99999999.99")

    response = auth_client(reporter).get(f"/api/clients/{client.pk}/statement/")

    wb = load_workbook(BytesIO(response.content), data_only=True)
    written = wb["Сводка"]["C8"].value
    # float(Decimal("99999999.99")) хранится как 99999999.98999999…, и Excel
    # показал бы копейку меньше. Decimal доезжает без потери.
    assert str(written) == "99999999.99"
    assert Decimal(str(written)) == Decimal("99999999.99")


def test_statement_can_include_multiple_selected_departments(
    auth_client, user_with_perms,
):
    reporter = user_with_perms(
        "statement-departments", codes=["clients.view", "reports.export"])
    north = Department.objects.create(code="north", name="Север", color="#315FD5")
    south = Department.objects.create(code="south", name="Юг", color="#1F9D6A")
    product = Product.objects.create(name="Мука", color="White", weight_kg="50")
    first = Client.objects.create(first_name="Клиент", last_name="Север", phone="1")
    second = Client.objects.create(first_name="Клиент", last_name="Юг", phone="2")
    north_order = Order.objects.create(
        client=first, status="shipped", department=north.code,
        settlement_intent="debt",
    )
    south_order = Order.objects.create(
        client=second, status="shipped", department=south.code,
        settlement_intent="debt",
    )
    OrderItem.objects.create(
        order=north_order, product=product, quantity=2, unit_price="100")
    OrderItem.objects.create(
        order=south_order, product=product, quantity=3, unit_price="100")

    response = auth_client(reporter).get(
        "/api/clients/statement/?departments=north,south")

    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content), data_only=True)
    order_departments = {
        workbook["Заказы"].cell(row=row, column=7).value
        for row in range(4, workbook["Заказы"].max_row + 1)
    }
    assert order_departments == {"Север", "Юг"}
    summary_departments = {
        workbook["Сводка"].cell(row=row, column=1).value
        for row in range(12, workbook["Сводка"].max_row + 1)
    }
    assert {"Север", "Юг"}.issubset(summary_departments)

    only_north = auth_client(reporter).get(
        "/api/clients/statement/?departments=north")
    north_workbook = load_workbook(BytesIO(only_north.content), data_only=True)
    assert north_workbook["Заказы"].max_row == 4
    assert north_workbook["Заказы"]["G4"].value == "Север"
    assert north_workbook["Клиенты"].max_row == 4
    assert north_workbook["Клиенты"]["B4"].value == first.name


def test_statement_uses_creation_shipping_and_payment_dates_consistently(
    auth_client, user_with_perms,
):
    from datetime import timedelta
    from django.utils import timezone

    reporter = user_with_perms(
        "statement-event-dates", codes=["clients.view", "reports.export"])
    department = Department.objects.create(
        code="date-dept", name="Отдел дат", color="#315FD5")
    client = Client.objects.create(first_name="Дата", last_name="Событий", phone="3")
    product = Product.objects.create(name="Крупа", color="White", weight_kg="25")
    now = timezone.now()
    long_ago = now - timedelta(days=30)

    shipped_today = Order.objects.create(
        client=client, status="shipped", department=department.code,
        settlement_intent="debt",
    )
    Order.objects.filter(pk=shipped_today.pk).update(created_at=long_ago)
    Shipment.objects.create(order=shipped_today, shipped_at=now)
    OrderItem.objects.create(
        order=shipped_today, product=product, quantity=2, unit_price="100")

    created_today = Order.objects.create(
        client=client, status="shipped", department=department.code,
        settlement_intent="debt",
    )
    Shipment.objects.create(order=created_today, shipped_at=long_ago)
    OrderItem.objects.create(
        order=created_today, product=product, quantity=5, unit_price="100")

    today = timezone.localdate().isoformat()
    response = auth_client(reporter).get(
        f"/api/clients/statement/?departments={department.code}"
        f"&date_from={today}&date_to={today}"
    )

    assert response.status_code == 200
    workbook = load_workbook(BytesIO(response.content), data_only=True)
    # Информационный лист заказов — по дате создания.
    assert workbook["Заказы"].max_row == 4
    assert workbook["Заказы"]["A4"].value == created_today.id
    # Финансовая продажа — по фактической дате отгрузки.
    assert workbook["Операции"].max_row == 4
    assert workbook["Операции"]["E4"].value == shipped_today.id
    assert workbook["Сводка"]["B8"].value == 1
    assert workbook["Сводка"]["C8"].value == 200
    assert workbook["Сводка"]["G5"].value == (
        f"{timezone.localdate():%d.%m.%Y} — {timezone.localdate():%d.%m.%Y}"
    )
    assert workbook["Сводка"]["F7"].value == "Баланс периода"
    # Долги обозначены как текущие и не теряются из-за фильтра периода.
    assert workbook["Долги"].max_row == 5


def test_statement_rejects_empty_or_unknown_department_selection(
    auth_client, user_with_perms,
):
    reporter = user_with_perms(
        "statement-bad-departments", codes=["clients.view", "reports.export"])

    empty = auth_client(reporter).get("/api/clients/statement/?departments=")
    unknown = auth_client(reporter).get(
        "/api/clients/statement/?departments=missing")

    assert empty.status_code == 400
    assert empty.data["code"] == "departments_required"
    assert unknown.status_code == 400
    assert unknown.data["code"] == "bad_department"
