import pytest
from datetime import date
from apps.clients.models import Client, Store
from apps.clients.services import is_payment_window_open, is_store_overdue

pytestmark = pytest.mark.django_db


def test_window_none_always_open():
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="S", payment_schedule_type="none")
    assert is_payment_window_open(s, date(2026, 6, 24)) is True


def test_store_overdue_only_on_scheduled_payment_day():
    # Без графика окно открыто всегда, но просрочкой это не считается.
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    free = Store.objects.create(client=c, name="S", payment_schedule_type="none")
    monthly = Store.objects.create(client=c, name="M",
                                   payment_schedule_type="monthly", payment_days=[5])
    assert is_store_overdue(free, date(2026, 6, 5)) is False
    assert is_store_overdue(monthly, date(2026, 6, 5)) is True
    assert is_store_overdue(monthly, date(2026, 6, 6)) is False


def test_window_monthly():
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="S",
                             payment_schedule_type="monthly", payment_days=[5, 20])
    assert is_payment_window_open(s, date(2026, 6, 5)) is True
    assert is_payment_window_open(s, date(2026, 6, 6)) is False


def test_window_weekly():
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="S",
                             payment_schedule_type="weekly", payment_days=[1, 5])
    assert is_payment_window_open(s, date(2026, 6, 22)) is True   # Monday
    assert is_payment_window_open(s, date(2026, 6, 23)) is False  # Tuesday


@pytest.mark.parametrize("days", ["5", {"5": True}, 5])
def test_window_with_broken_days_does_not_crash(days):
    # Старые записи могли прийти в обход формы: дни не списком. Приём оплаты
    # и списки долгов не должны падать 500-кой, а график, который не прочесть,
    # не блокирует оплату — как и неизвестный тип.
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="S",
                             payment_schedule_type="monthly", payment_days=days)
    assert is_payment_window_open(s, date(2026, 6, 5)) is True
