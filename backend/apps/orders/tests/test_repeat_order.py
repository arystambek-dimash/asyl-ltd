import pytest
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem, Payment
from apps.warehouse.models import StockItem

pytestmark = pytest.mark.django_db


def test_repeat_creates_independent_order_for_today(auth_client, manager):
    client = Client.objects.create(first_name="Нью", last_name="Сити", phone="1")
    product = Product.objects.create(name="Мука", color="Red", weight_kg="50")
    StockItem.objects.create(product=product, bags=100)
    source = Order.objects.create(
        client=client, currency="USD", department="main", status="shipped",
        payment_status="partial", settlement_intent="debt", payment_method="debt",
        truck_number="123ABC02", arrival_date=timezone.localdate(),
        loading_camera="cam2", created_by=manager,
    )
    OrderItem.objects.create(
        order=source, product=product, quantity=7, unit_price="11.50",
    )
    Payment.objects.create(
        order=source, amount="10", method="cash", status="confirmed",
    )

    response = auth_client(manager).post(f"/api/orders/{source.pk}/repeat/")

    assert response.status_code == 201
    repeated = Order.objects.get(pk=response.data["id"])
    assert repeated.pk != source.pk
    assert repeated.repeated_from == source
    assert timezone.localtime(repeated.created_at).date() == timezone.localdate()
    assert repeated.arrival_date == timezone.localdate()
    assert repeated.status == "confirmed"
    assert repeated.currency == "USD"
    assert repeated.client == source.client
    assert repeated.truck_number == source.truck_number
    assert repeated.loading_camera == ""
    assert repeated.payment_status == "unpaid"
    assert repeated.payments.count() == 0
    item = repeated.items.get()
    assert (item.product, item.quantity, str(item.unit_price)) == (product, 7, "11.50")
    event = EventLog.objects.get(event_type="order_repeat", order=repeated)
    assert event.payload["source_order_id"] == source.pk


def test_repeat_without_confirm_permission_stays_pending(auth_client, user_with_perms):
    user = user_with_perms("repeater", codes=["orders.view", "orders.create"])
    client = Client.objects.create(first_name="A", last_name="B", phone="2")
    product = Product.objects.create(name="Товар", color="Blue", weight_kg="50")
    StockItem.objects.create(product=product, bags=10)
    source = Order.objects.create(client=client, status="shipped")
    OrderItem.objects.create(order=source, product=product, quantity=1, unit_price="100")

    response = auth_client(user).post(f"/api/orders/{source.pk}/repeat/")

    assert response.status_code == 201
    assert Order.objects.get(pk=response.data["id"]).status == "pending"


def test_repeat_rejects_deleted_product_snapshot(auth_client, manager):
    client = Client.objects.create(first_name="A", last_name="C", phone="3")
    source = Order.objects.create(client=client, status="shipped")
    OrderItem.objects.create(
        order=source, product=None, product_label_snapshot="Архивный товар",
        quantity=1, unit_price="100",
    )

    response = auth_client(manager).post(f"/api/orders/{source.pk}/repeat/")

    assert response.status_code == 400
    assert response.data["code"] == "repeat_product_unavailable"
    assert Order.objects.count() == 1
