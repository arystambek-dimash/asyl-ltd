import pytest
from django.contrib import admin
from django.test import RequestFactory

from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem, StockMovement, StockReceipt

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "model",
    [
        Order,
        OrderItem,
        Payment,
        Shipment,
        Client,
        StockItem,
        StockReceipt,
        StockMovement,
    ],
)
def test_operational_admin_is_read_only(model, admin_user):
    request = RequestFactory().get("/admin/")
    request.user = admin_user
    model_admin = admin.site._registry[model]

    assert model_admin.has_view_permission(request) is True
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False


def test_stock_movement_admin_keeps_operational_list_columns():
    model_admin = admin.site._registry[StockMovement]

    assert model_admin.list_display == (
        "warehouse",
        "product",
        "delta",
        "balance_after",
        "reason",
        "created_at",
        "created_by",
    )
    assert model_admin.list_filter == ("warehouse", "reason", "product")
