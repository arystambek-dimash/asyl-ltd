from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.eventlog.models import EventLog
from apps.orders import apipay
from apps.orders.apipay import create_cash_refund
from apps.orders.models import Order, Payment, PaymentRefund
from apps.orders.reports import summary_report
from apps.orders.services import record_staff_payment, reopen_confirmed_payment, accountant_confirm_payment
from apps.orders.tests.test_payment_regressions import _order
from apps.orders.tests.test_apipay_refund_reconciliation import _invoice

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('mode', ['cash', 'apipay'])
@pytest.mark.parametrize('archived_field', ['deleted_at', 'purged_at'])
def test_archived_order_rejects_new_refund(auth_client, accountant, mode, archived_field):
    invoice = _invoice()
    archived = {'deleted_at': timezone.now()}
    if archived_field == 'purged_at':
        archived['purged_at'] = timezone.now()
    Order.all_objects.filter(pk=invoice.payment.order_id).update(**archived)
    with patch('apps.orders.apipay.api_request') as provider:
        response = auth_client(accountant).post(
            f'/api/payment-transactions/{invoice.payment_id}/refund/',
            {'mode': mode, 'amount': '20', 'reason': 'Возврат'},
        )
    assert response.status_code == 400
    assert response.data['code'] == 'order_not_active'
    provider.assert_not_called()
    assert not PaymentRefund.objects.exists()


def test_archive_between_reservation_and_provider_call_releases_refund(accountant, monkeypatch):
    invoice = _invoice()
    fence = apipay._provider_scope_fence

    @contextmanager
    def archive_then_lock(order_id, user, **kwargs):
        Order.all_objects.filter(pk=order_id).update(deleted_at=timezone.now())
        with fence(order_id, user, **kwargs) as order:
            yield order

    monkeypatch.setattr(apipay, '_provider_scope_fence', archive_then_lock)
    with patch('apps.orders.apipay.api_request') as provider:
        with pytest.raises(ValidationError) as error:
            apipay.create_refund(invoice, accountant, amount='20', reason='Возврат')
    assert error.value.detail['code'] == 'order_not_active'
    provider.assert_not_called()
    assert PaymentRefund.objects.get(payment=invoice.payment).status == 'failed'
    invoice.payment.refresh_from_db()
    assert invoice.payment.pending_refund_amount == 0
    assert invoice.payment.refunded_amount == 0


def test_cash_refund_cannot_be_reopened_and_keeps_income(auth_client, accountant):
    order = _order()
    payment = record_staff_payment(order, '100', accountant)
    create_cash_refund(payment, accountant, amount='20', reason='Частичный возврат')
    response = auth_client(accountant).post(f'/api/orders/{order.pk}/payments/{payment.pk}/reopen/')
    assert response.status_code == 400
    assert response.data['code'] == 'payment_has_refunds'
    payment.refresh_from_db()
    assert payment.status == 'confirmed'
    assert payment.net_amount == Decimal('80')
    assert summary_report(Order.objects.all(), income_only=True)['income']['total'] == '80.00'
    events = auth_client(accountant).get('/api/orders/cashier-log/').data
    assert not any(row['can_reopen'] for row in events)


@pytest.mark.parametrize('status,allowed', [('pending', False), ('completed', False), ('failed', True)])
def test_refund_ledger_prevents_reopen_even_if_cached_amounts_drift(accountant, status, allowed):
    payment = record_staff_payment(_order(), '100', accountant)
    PaymentRefund.objects.create(payment=payment, amount='10', method='cash', status=status, reason='Проверка')
    if allowed:
        reopen_confirmed_payment(payment, accountant)
        assert payment.status == 'received'
    else:
        with pytest.raises(ValidationError) as error:
            reopen_confirmed_payment(payment, accountant)
        assert error.value.detail['code'] == 'payment_has_refunds'


def test_journal_only_latest_confirmation_can_reopen_across_pages_and_dates(auth_client, accountant):
    payment = record_staff_payment(_order(), '100', accountant)
    old_event = EventLog.objects.filter(payload__payment_id=payment.pk, payload__payment_stage='confirmed').get()
    yesterday = timezone.now() - timedelta(days=1)
    EventLog.objects.filter(pk=old_event.pk).update(created_at=yesterday)
    reopen_confirmed_payment(payment, accountant)
    accountant_confirm_payment(payment, accountant)
    latest = EventLog.objects.filter(payload__payment_id=payment.pk, payload__payment_stage='confirmed').first()
    for _ in range(52):
        EventLog.objects.create(event_type='payment', order=payment.order, message='Проверка журнала')
    client = auth_client(accountant)
    old_page = client.get('/api/orders/cashier-log/?page=2&page_size=50')
    rows = old_page.data['results']
    assert next(row for row in rows if row['id'] == latest.pk)['can_reopen'] is True
    assert next(row for row in rows if row['id'] == old_event.pk)['can_reopen'] is False
    day = timezone.localdate(yesterday).isoformat()
    old_period = client.get(f'/api/orders/cashier-log/?date_from={day}&date_to={day}')
    assert next(row for row in old_period.data if row['id'] == old_event.pk)['can_reopen'] is False


def test_income_projection_matches_full_report_with_two_queries(accountant):
    first = record_staff_payment(_order(), '100', accountant)
    create_cash_refund(first, accountant, amount='20', reason='Возврат')
    from apps.orders.models import OrderItem
    second = Order.objects.create(client=first.order.client, status='shipped', currency='USD')
    OrderItem.objects.create(order=second, product=first.order.items.first().product, quantity=1, unit_price='5')
    record_staff_payment(second, '5', accountant)
    full = summary_report(Order.objects.all())
    with CaptureQueriesContext(connection) as queries:
        compact = summary_report(Order.objects.all(), income_only=True)
    assert compact == {key: full[key] for key in ('from', 'to', 'income')}
    assert len(queries) == 2


@pytest.mark.django_db(transaction=True)
def test_refund_racing_reopen_cannot_commit_both(accountant):
    payment = record_staff_payment(_order(), '100', accountant)
    barrier = Barrier(2)
    def run(action):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            row = Payment.objects.get(pk=payment.pk)
            try:
                if action == 'refund':
                    create_cash_refund(row, accountant, amount='20', reason='Возврат')
                else:
                    reopen_confirmed_payment(row, accountant)
                return True
            except ValidationError:
                return False
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, ['refund', 'reopen']))
    assert sorted(outcomes) == [False, True]
    payment.refresh_from_db()
    assert (payment.status, payment.refunded_amount) in [('confirmed', Decimal('20')), ('received', Decimal('0'))]
