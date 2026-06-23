import pytest
from decimal import Decimal
from catalog.models import Product
from clients.models import Client
from orders.models import Order, OrderItem, Payment
from warehouse.services import receive_stock
from webhooks.models import Camera, WebhookCall
from webhooks.services import process_webhook, normalize_plate

pytestmark = pytest.mark.django_db


def _camera(kind, tpl=""):
    return Camera.objects.create(name=kind, camera_id=f"{kind}-01", kind=kind,
                                 api_key="k", response_template=tpl)


def _paid_order(boss, status="paid", plate="123ABC02", bags_stock=100, qty=50):
    prod = Product.objects.create(name="Премиум", color="Red", weight_kg="50", price="100.00")
    receive_stock(prod, bags_stock, boss)
    c = Client.objects.create(first_name="И", last_name="П", phone="x")
    o = Order.objects.create(client=c, status=status, truck_number=plate)
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    if status in ("paid", "arrived", "loading"):
        Payment.objects.create(order=o, amount=o.total_amount)
    return o, prod


def test_normalize_plate():
    assert normalize_plate("123 abc 02") == "123ABC02"


def test_entry_allows_paid_order(boss):
    o, _ = _paid_order(boss, status="paid")
    cam = _camera("entry", '{"open": {{allowed}}, "order": {{order_id}}}')
    resp = process_webhook(cam, {"plate": "123 ABC 02"})
    o.refresh_from_db()
    assert o.status == "arrived"
    assert resp == {"open": True, "order": o.id}
    call = WebhookCall.objects.get()
    assert call.decision == "allow" and call.matched_order_id == o.id


def test_entry_denies_unpaid(boss):
    o, _ = _paid_order(boss, status="confirmed")
    cam = _camera("entry")
    resp = process_webhook(cam, {"plate": "123ABC02"})
    assert resp["decision"] == "deny"
    assert "оплач" in resp["reason"].lower()
    o.refresh_from_db()
    assert o.status == "confirmed"


def test_entry_denies_no_order():
    cam = _camera("entry")
    resp = process_webhook(cam, {"plate": "999ZZZ99"})
    assert resp["decision"] == "deny"


def test_counter_records_bags(boss):
    o, _ = _paid_order(boss, status="paid")
    process_webhook(_camera("entry"), {"plate": "123ABC02"})
    cam = _camera("counter")
    resp = process_webhook(cam, {"plate": "123ABC02", "bags": 50})
    o.refresh_from_db()
    assert o.status == "loading" and resp["decision"] == "allow"
    assert o.shipment.bags_loaded == 50


def test_exit_records_shipment(boss):
    o, prod = _paid_order(boss, status="paid")
    process_webhook(_camera("entry"), {"plate": "123ABC02"})
    process_webhook(_camera("counter"), {"plate": "123ABC02", "bags": 50})
    o.refresh_from_db()
    o.shipment.weigh_in_kg = Decimal("8000")
    o.shipment.save()
    # Exit camera fires after the operator confirms loading is done (loaded).
    from shipments.services import finish_loading
    finish_loading(o, boss)
    cam = _camera("exit")
    resp = process_webhook(cam, {"plate": "123ABC02", "weight_kg": 10500})
    o.refresh_from_db()
    assert o.status == "shipped" and resp["decision"] == "allow"


def test_last_seen_updated(boss):
    cam = _camera("entry")
    assert cam.last_seen is None
    process_webhook(cam, {"plate": "X"})
    cam.refresh_from_db()
    assert cam.last_seen is not None
