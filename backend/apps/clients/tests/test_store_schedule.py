import pytest
from datetime import date
from apps.clients.models import Client, Store
from apps.clients.services import is_payment_window_open

pytestmark = pytest.mark.django_db


def test_window_none_always_open():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="S", payment_schedule_type="none")
    assert is_payment_window_open(s, date(2026, 6, 24)) is True


def test_window_monthly():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="S",
                             payment_schedule_type="monthly", payment_days=[5, 20])
    assert is_payment_window_open(s, date(2026, 6, 5)) is True
    assert is_payment_window_open(s, date(2026, 6, 6)) is False


def test_window_weekly():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="S",
                             payment_schedule_type="weekly", payment_days=[1, 5])
    assert is_payment_window_open(s, date(2026, 6, 22)) is True   # Monday
    assert is_payment_window_open(s, date(2026, 6, 23)) is False  # Tuesday
