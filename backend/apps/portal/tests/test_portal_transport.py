"""Портал: клиент указывает тягач и прицеп сам, номер сотрудника — только для чтения."""
from decimal import Decimal

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.notifications.models import Notification
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


@pytest.fixture
def portal(make_user):
    user = make_user(username="cli", client=True)
    client = Client.objects.create_with_user(
        user=user, first_name="A", last_name="B", phone="portal-plates", country="Кыргызстан")
    product = Product.objects.create(name="F", color="Red", weight_kg=Decimal("50"), price=Decimal("100"))
    order = Order.objects.create(client=client, status="confirmed")
    OrderItem.objects.create(order=order, product=product, quantity=1, unit_price=Decimal("100"))
    return user, order


def _patch(auth_client, user, order, payload):
    return auth_client(user).patch(f"/api/portal/orders/{order.pk}/truck/", payload, format="json")


def test_client_sets_truck_and_trailer(portal, auth_client):
    user, order = portal

    response = _patch(auth_client, user, order, {"truck_number": "07 kg 695 adt", "trailer_number": "07kg837pb"})

    assert response.status_code == 200, response.data
    assert (response.data["truck_number"], response.data["trailer_number"]) == ("07KG695ADT", "07KG837PB")
    assert response.data["transport_locked"] is False
    # Страна клиента — страна номера по умолчанию в поле портала.
    assert response.data["client_country"] == "Кыргызстан"
    order.refresh_from_db()
    assert (order.truck_number, order.trailer_number, order.truck_number_set_by) == ("07KG695ADT", "07KG837PB", user)


@pytest.mark.parametrize(
    "payload", [{"truck_number": "X" * 31}, {"truck_number": "403BJN13", "trailer_number": "Y" * 40},
                {"truck_number": "1234567890123"}],
)
def test_too_long_numbers_are_a_400(portal, auth_client, payload):
    user, order = portal

    assert _patch(auth_client, user, order, payload).status_code == 400


def test_repeating_the_same_number_changes_nothing(portal, auth_client):
    user, order = portal

    assert _patch(auth_client, user, order, {"truck_number": "403BJN13"}).status_code == 200
    assert _patch(auth_client, user, order, {"truck_number": "403 bjn 13"}).status_code == 200

    assert EventLog.objects.filter(order=order, event_type="status").count() == 1
    assert not Notification.objects.filter(client=order.client).exists()


def test_number_set_by_staff_is_read_only_for_client(portal, auth_client, manager):
    user, order = portal
    Order.objects.filter(pk=order.pk).update(truck_number="403BJN13", truck_number_set_by=manager)

    detail = auth_client(user).get(f"/api/portal/orders/{order.pk}/")
    change = _patch(auth_client, user, order, {"truck_number": "612BEX13"})

    assert detail.data["transport_locked"] is True
    assert change.status_code == 400
    assert change.data["code"] == "forbidden"


def test_number_is_read_only_after_arrival(portal, auth_client):
    user, order = portal
    Order.objects.filter(pk=order.pk).update(status="arrived", truck_number="403BJN13", truck_number_set_by=user)

    assert auth_client(user).get(f"/api/portal/orders/{order.pk}/").data["transport_locked"] is True


@pytest.mark.parametrize("status", ["arrived", "loading", "loaded", "shipped"])
def test_number_is_hidden_once_the_truck_is_on_site(portal, auth_client, manager, status):
    """Машина на территории — номер клиенту не показывается совсем (решение владельца)."""
    user, order = portal
    Order.objects.filter(pk=order.pk).update(
        status=status, truck_number="403BJN13", trailer_number="07KG837PB", truck_number_set_by=manager)

    detail = auth_client(user).get(f"/api/portal/orders/{order.pk}/").data
    [row] = auth_client(user).get("/api/portal/orders/").data

    for data in (detail, row):
        assert (data["truck_number"], data["trailer_number"]) == ("", "")


def test_number_is_shown_before_arrival(portal, auth_client, manager):
    user, order = portal
    Order.objects.filter(pk=order.pk).update(
        truck_number="403BJN13", trailer_number="07KG837PB", truck_number_set_by=manager)

    detail = auth_client(user).get(f"/api/portal/orders/{order.pk}/").data

    assert (detail["truck_number"], detail["trailer_number"]) == ("403BJN13", "07KG837PB")
