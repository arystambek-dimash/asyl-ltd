"""POS кассы: Kaspi QR через ApiPay по заказу в долге и опрос статуса оплаты."""
import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.apipay import ApiPayAPIError
from apps.orders.models import Order, OrderItem, Payment
from apps.orders.tests.apipay_fakes import ProviderResponse

pytestmark = pytest.mark.django_db


QR_RESPONSE = {
    "id": 77,
    "status": "pending",
    "qr_token_url": "https://qr.kaspi.kz/pos",
    "qr_image_url": "https://api.apipay.kz/qr/pos.png",
    "qr_expires_at": "2026-09-12T09:05:00+00:00",
}


def _debt_order(*, total="1000.00", currency="KZT"):
    client = Client.objects.create_with_user(
        first_name="Долговой",
        last_name="Клиент",
        phone="87762838451",
    )
    # get_or_create: Product.Meta.unique_together = ("name", "color", "weight_kg")
    # would collide when a test creates two debt orders (e.g. the cross-order
    # scoping test) — both orders can safely share one product row.
    product, _ = Product.objects.get_or_create(
        name="POS товар",
        color="Red",
        weight_kg="50",
    )
    order = Order.objects.create(
        client=client,
        status="shipped",
        currency=currency,
        settlement_intent="debt",
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        quantity=1,
        unit_price=Decimal(total),
    )
    return order


@pytest.fixture
def apipay(settings, apipay_department):
    settings.APIPAY_BASE_URL = "https://api.apipay.kz/api/v1"
    with patch("apps.orders.apipay.urllib.request.urlopen") as urlopen:
        urlopen.return_value = ProviderResponse(QR_RESPONSE)
        yield urlopen


def _pos_qr(auth_client, user, order, amount="1000", channel="qr"):
    return auth_client(user).post(
        f"/api/orders/{order.id}/payments/",
        {"method": "kaspi", "channel": channel, "amount": amount},
        format="json",
    )


def test_pos_qr_issues_an_apipay_qr_and_keeps_the_debt(auth_client, accountant, apipay):
    order = _debt_order()

    response = _pos_qr(auth_client, accountant, order, amount="400")

    assert response.status_code == 201, response.data
    request = apipay.call_args.args[0]
    assert request.full_url == "https://api.apipay.kz/api/v1/invoices/qr"
    assert Decimal(str(json.loads(request.data)["amount"])) == Decimal("400")
    assert response.data["status"] == "requested"
    assert response.data["method"] == "kaspi"
    assert response.data["provider"]["channel"] == "qr"
    assert response.data["provider"]["qr_image_url"] == QR_RESPONSE["qr_image_url"]
    order.refresh_from_db()
    # Касса гасит долг: заказ не должен превратиться в «моментальную оплату».
    assert order.settlement_intent == "debt"
    assert order.is_debt


@pytest.mark.parametrize(
    ("currency", "channel", "amount", "code"),
    [
        ("USD", "qr", "1000", "apipay_kzt_only"),
        ("KZT", "qr", "10.50", "qr_whole_tenge"),
        ("KZT", "phone", "100", "invalid_payment_channel"),
    ],
)
def test_pos_qr_rejects_invalid_requests(
    auth_client, accountant, apipay, currency, channel, amount, code,
):
    order = _debt_order(currency=currency)

    response = _pos_qr(auth_client, accountant, order, amount=amount, channel=channel)

    assert response.status_code == 400
    assert response.data["code"] == code
    assert not Payment.objects.filter(order=order).exists()
    apipay.assert_not_called()


def test_pos_qr_provider_failure_rejects_the_payment(auth_client, accountant):
    order = _debt_order()

    with patch(
        "apps.orders.views.create_invoice",
        side_effect=ApiPayAPIError(503, "provider_unavailable", "Недоступно", {}),
    ):
        response = _pos_qr(auth_client, accountant, order)

    assert response.status_code == 400
    assert response.data["code"] == "provider_unavailable"
    assert set(order.payments.values_list("status", flat=True)) == {"rejected"}


def test_payment_detail_returns_the_provider_state(auth_client, accountant, apipay):
    order = _debt_order()
    created = _pos_qr(auth_client, accountant, order, amount="100")

    response = auth_client(accountant).get(
        f"/api/orders/{order.id}/payments/{created.data['id']}/"
    )

    assert response.status_code == 200
    assert response.data["id"] == created.data["id"]
    assert response.data["status"] == "requested"
    assert response.data["provider"]["qr_token_url"] == QR_RESPONSE["qr_token_url"]


def test_payment_detail_is_scoped_to_its_order(auth_client, accountant, apipay):
    order = _debt_order()
    other = _debt_order()
    created = _pos_qr(auth_client, accountant, order, amount="100")

    response = auth_client(accountant).get(
        f"/api/orders/{other.id}/payments/{created.data['id']}/"
    )

    assert response.status_code == 404


def test_payment_detail_requires_payment_permissions(
    auth_client, accountant, manager, apipay
):
    order = _debt_order()
    created = _pos_qr(auth_client, accountant, order, amount="100")

    response = auth_client(manager).get(
        f"/api/orders/{order.id}/payments/{created.data['id']}/"
    )

    assert response.status_code == 403
