import pytest
from rest_framework.exceptions import ValidationError
from apps.clients.models import Client
from apps.orders.models import Order
from apps.orders import services


@pytest.fixture
def make_order(db):
    def _make(status="pending"):
        c = Client.objects.create(first_name="A", last_name="B", phone="1")
        return Order.objects.create(client=c, status=status)
    return _make


def test_confirm_from_pending(make_order, make_user):
    o = make_order("pending")
    services.confirm_order(o, make_user())
    assert o.status == "confirmed"


def test_reject_from_pending(make_order, make_user):
    o = make_order("pending")
    services.reject_order(o, make_user())
    assert o.status == "rejected"


def test_cannot_reject_confirmed(make_order, make_user):
    o = make_order("confirmed")
    with pytest.raises(ValidationError):
        services.reject_order(o, make_user())


def test_transition_rejects_illegal(make_order, make_user):
    o = make_order("pending")
    with pytest.raises(ValidationError):
        services.transition(o, "shipped", make_user())
