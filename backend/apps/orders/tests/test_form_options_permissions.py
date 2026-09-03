import pytest
from rest_framework.test import APIClient

from apps.catalog.models import ClientPrice, Product
from apps.clients.models import Client, Store
from apps.orders.models import Order, OrderItem
from apps.sales.models import Department
from apps.warehouse.models import StockItem, Warehouse

pytestmark = pytest.mark.django_db


def _api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _reference_rows():
    client = Client.objects.create_with_user(
        first_name="Айжан",
        last_name="Серикова",
        company_name="ТОО Север",
        phone="+77010000000",
        country="Казахстан",
        iin="123456789012",
        bank="Скрытый банк",
        bank_account="KZ000000000000000000",
        currency="USD",
    )
    store = Store.objects.create(
        client=client,
        name="Север-1",
        address="Промзона 5",
        phone="+77020000000",
        payment_schedule_type="weekly",
        payment_days=[1],
    )
    product = Product.objects.create(
        name="Мука",
        color="Blue",
        weight_kg="50",
        ask_truck_weight=True,
    )
    StockItem.objects.create(product=product, bags=37)
    department = Department.objects.create(
        code="order-options-test",
        name="Оптовый отдел",
        color="#123456",
        is_active=True,
    )
    return client, store, product, department


@pytest.mark.parametrize("permission", ["orders.create", "orders.edit"])
def test_order_permission_grants_minimal_form_options(
    user_with_perms,
    permission,
):
    user = user_with_perms(f"options-{permission}", codes=[permission])
    client, store, product, department = _reference_rows()

    response = _api(user).get("/api/orders/form-options/")

    assert response.status_code == 200
    clients = {row["id"]: row for row in response.data["clients"]}
    products = {row["id"]: row for row in response.data["products"]}
    warehouses = {row["id"]: row for row in response.data["warehouses"]}
    stores = {row["id"]: row for row in response.data["stores"]}
    departments = {
        row["id"]: row for row in response.data["departments"]
    }
    assert clients[client.id] == {
        "id": client.id,
        "name": "Айжан Серикова",
        "company_name": "ТОО Север",
        "phone": "+77010000000",
        "currency": "USD",
    }
    main = Warehouse.objects.get(is_default=True)
    assert products[product.id] == {
        "id": product.id,
        "label": "Мука · Синий 50 кг",
        "available_bags": 37,
        "warehouse": main.pk,
        "warehouse_name": main.name,
        "stock_by_warehouse": {str(main.pk): 37},
    }
    assert warehouses[main.pk]["code"] == "main"
    assert warehouses[main.pk]["is_default"] is True
    assert stores[store.id] == {
        "id": store.id,
        "client": client.id,
        "name": "Север-1",
        "address": "Промзона 5",
    }
    assert departments[department.id] == {
        "id": department.id,
        "code": "order-options-test",
        "name": "Оптовый отдел",
        "color": "#123456",
        "is_default": False,
    }
    # No financial, legal, schedule, catalog-management or CV fields leak.
    assert {
        "iin",
        "bank",
        "bank_account",
        "country",
        "debt_total",
    }.isdisjoint(clients[client.id])
    assert {
        "phone",
        "payment_schedule_type",
        "payment_days",
        "contract_signed_at",
    }.isdisjoint(stores[store.id])
    assert {
        "color",
        "cv_class",
        "ask_truck_weight",
        "is_active",
    }.isdisjoint(products[product.id])


def test_form_options_does_not_replace_generic_reference_permissions(
    user_with_perms,
):
    user = user_with_perms("order-options-only", codes=["orders.create"])
    _reference_rows()
    api = _api(user)

    assert api.get("/api/orders/form-options/").status_code == 200
    assert api.get("/api/clients/").status_code == 403
    assert api.get("/api/products/").status_code == 403
    assert api.get("/api/stores/").status_code == 403


def test_form_options_exposes_one_product_with_balances_by_warehouse(
    user_with_perms,
):
    user = user_with_perms("multi-stock-options", codes=["orders.create"])
    _client, _store, product, _department = _reference_rows()
    main = Warehouse.objects.get(is_default=True)
    secondary = Warehouse.objects.create(code="mill-2", name="Мельница 2")
    inactive = Warehouse.objects.create(
        code="closed",
        name="Закрытый склад",
        is_active=False,
    )
    StockItem.objects.create(product=product, warehouse=secondary, bags=12)
    StockItem.objects.create(product=product, warehouse=inactive, bags=99)

    response = _api(user).get("/api/orders/form-options/")

    assert response.status_code == 200
    rows = [row for row in response.data["products"] if row["id"] == product.pk]
    assert len(rows) == 1
    assert rows[0]["stock_by_warehouse"] == {
        str(main.pk): 37,
        str(secondary.pk): 12,
    }
    assert rows[0]["warehouse"] == main.pk
    assert rows[0]["available_bags"] == 37
    assert str(inactive.pk) not in rows[0]["stock_by_warehouse"]
    assert inactive.pk not in {
        warehouse["id"] for warehouse in response.data["warehouses"]
    }


def test_unrelated_order_permission_cannot_read_form_options(user_with_perms):
    viewer = user_with_perms("orders-view-only", codes=["orders.view"])

    assert _api(viewer).get("/api/orders/form-options/").status_code == 403


def test_create_only_user_can_submit_selected_reference(user_with_perms):
    creator = user_with_perms("orders-create-only", codes=["orders.create"])
    client, _store, product, department = _reference_rows()

    response = _api(creator).post(
        "/api/orders/",
        {
            "client": client.id,
            "department": department.code,
            "items": [{"product": product.id, "quantity": 2}],
        },
        format="json",
    )

    assert response.status_code == 201
    assert Order.objects.get(pk=response.data["id"]).client_id == client.id


def test_edit_only_user_can_validate_existing_client_and_load_prices(
    user_with_perms,
):
    editor = user_with_perms("orders-edit-only", codes=["orders.edit"])
    client, _store, product, department = _reference_rows()
    order = Order.objects.create(
        client=client,
        department=department.code,
        status="pending",
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        quantity=1,
        unit_price="125.00",
    )
    ClientPrice.objects.create(
        client=client,
        product=product,
        currency="USD",
        price="125.00",
    )
    api = _api(editor)

    update = api.patch(
        f"/api/orders/{order.id}/",
        {"client": client.id, "notes": "Уточнено"},
        format="json",
    )
    prices = api.get(
        f"/api/client-prices/?client={client.id}&currency=USD"
    )

    assert update.status_code == 200
    assert prices.status_code == 200
    assert prices.data == {str(product.id): "125.00"}
