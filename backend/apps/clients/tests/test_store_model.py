import pytest
from apps.clients.models import Client, Store

pytestmark = pytest.mark.django_db


def test_store_belongs_to_client_with_schedule():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="Магазин №1",
                             payment_schedule_type="monthly", payment_days=[5, 20])
    assert s in c.stores.all()
    assert s.payment_days == [5, 20]
    assert "monthly" in Store.SCHEDULE_TYPES
