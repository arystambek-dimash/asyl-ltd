from decimal import Decimal
from unittest.mock import patch

import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.cameras.models import AiCountingSession
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders import services as order_services
from apps.orders.models import Order, OrderItem
from apps.orders.serializers import OrderSerializer
from apps.shipments import scale
from apps.shipments.models import Shipment
from apps.shipments.services import (
    begin_camera_loading,
    ensure_scale_entry_weight,
    finish_loading,
    record_arrival,
    record_count,
)
from apps.warehouse.models import StockItem
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


def _reading(weight: str) -> scale.ScaleReading:
    return scale.ScaleReading(
        weight_kg=Decimal(weight),
        age_seconds=Decimal("0.2"),
        updated_at="2026-08-10T16:00:00+05:00",
    )


def _order(boss, *, status="confirmed") -> tuple[Order, Product]:
    product = Product.objects.create(
        name="Весовой товар",
        color="Blue",
        weight_kg="50",
        price="100.00",
    )
    receive_stock(product, 100, boss)
    client = Client.objects.create_with_user(
        first_name="Scale",
        last_name="Flow",
        phone="scale-flow",
    )
    order = Order.objects.create(
        client=client,
        status=status,
        truck_number="01SCALE",
    )
    OrderItem.objects.create(order=order, product=product, quantity=50)
    return order, product


def _loading_scale_order(settings, boss, operator) -> tuple[Order, Product]:
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    order, product = _order(boss)
    with patch.object(scale, "read_truck_scale", return_value=_reading("10000")):
        ensure_scale_entry_weight(order, operator)
    begin_camera_loading(order, "cam2", operator)
    record_count(order, 50, operator)
    order.refresh_from_db()
    return order, product


def test_entry_weight_is_real_scale_reading_and_is_idempotent(
    settings, boss, operator,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    order, _product = _order(boss)

    with patch.object(
        scale, "read_truck_scale", return_value=_reading("9876.50")
    ) as read:
        first = ensure_scale_entry_weight(order, operator)
        second = ensure_scale_entry_weight(order, operator)

    assert first is not None
    assert second is not None
    assert second.pk == first.pk
    assert second.weigh_in_kg == Decimal("9876.50")
    assert second.weigh_in_source == Shipment.WeightSource.SCALE
    assert second.weigh_out_kg is None
    assert second.net_weight_kg is None
    read.assert_called_once_with()


def test_direct_begin_cannot_create_estimated_entry_for_new_required_order(
    settings, boss, operator,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    order, _product = _order(boss)

    with pytest.raises(ValidationError) as exc_info:
        begin_camera_loading(order, "cam2", operator)

    assert exc_info.value.detail["code"] == "scale_entry_weight_required"
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert order.loading_camera == ""
    assert not Shipment.objects.filter(order=order).exists()


def test_manual_arrival_service_cannot_bypass_required_scale_entry(
    settings, boss, operator,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    order, _product = _order(boss)

    with pytest.raises(ValidationError) as exc_info:
        record_arrival(order, Decimal("10000"), operator)

    assert exc_info.value.detail["code"] == "scale_entry_weight_required"
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert not Shipment.objects.filter(order=order).exists()


def test_finish_records_exit_and_net_then_ships_atomically(
    settings, boss, operator,
):
    order, product = _loading_scale_order(settings, boss, operator)

    with patch.object(
        scale, "read_truck_scale", return_value=_reading("12500")
    ) as read:
        shipment = finish_loading(order, operator)

    order.refresh_from_db()
    shipment.refresh_from_db()
    assert read.call_count == 1
    assert order.status == "shipped"
    assert shipment.weigh_in_kg == Decimal("10000")
    assert shipment.weigh_out_kg == Decimal("12500")
    assert shipment.net_weight_kg == Decimal("2500")
    assert StockItem.objects.get(product=product).bags == 50
    serialized = OrderSerializer(order).data
    assert serialized["weigh_in_source"] == "scale"
    assert Decimal(serialized["weigh_out_kg"]) == Decimal("12500")
    assert Decimal(serialized["net_weight_kg"]) == Decimal("2500")


def test_invalid_exit_weight_does_not_ship_or_deduct_stock(
    settings, boss, operator,
):
    order, product = _loading_scale_order(settings, boss, operator)

    with patch.object(
        scale, "read_truck_scale", return_value=_reading("9999")
    ), pytest.raises(ValidationError) as exc_info:
        finish_loading(order, operator)

    assert exc_info.value.detail["code"] == "invalid_scale_weight_direction"
    order.refresh_from_db()
    order.shipment.refresh_from_db()
    assert order.status == "loading"
    assert order.shipment.weigh_out_kg is None
    assert order.shipment.net_weight_kg is None
    assert StockItem.objects.get(product=product).bags == 100


def test_not_ready_exit_does_not_open_database_transition(
    settings, boss, operator,
):
    order, product = _loading_scale_order(settings, boss, operator)

    with patch.object(
        scale, "read_truck_scale", side_effect=scale.TruckScaleNotReady()
    ), pytest.raises(scale.TruckScaleNotReady):
        finish_loading(order, operator)

    order.refresh_from_db()
    assert order.status == "loading"
    assert order.shipment.weigh_out_kg is None
    assert StockItem.objects.get(product=product).bags == 100


def test_pre_deploy_legacy_loading_finishes_without_false_net(
    settings, boss, operator,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    order, _product = _order(boss)
    # Migration 0030 marks all rows that existed at rollout as legacy. New
    # model instances default True, so reproduce that migrated row explicitly.
    order.scale_weighing_required = False
    order.save(update_fields=["scale_weighing_required"])
    record_arrival(order, Decimal("8000"), operator)
    record_count(order, 50, operator)

    with patch.object(scale, "read_truck_scale") as read:
        shipment = finish_loading(order, operator)

    read.assert_not_called()
    shipment.refresh_from_db()
    assert shipment.weigh_in_source == Shipment.WeightSource.MANUAL
    assert shipment.weigh_out_kg is None
    assert shipment.net_weight_kg is None


def test_arrival_endpoint_reads_scale_server_side_when_weight_is_omitted(
    settings, boss,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    order, _product = _order(boss)
    api = APIClient()
    api.force_authenticate(boss)

    with patch.object(
        scale, "read_truck_scale", return_value=_reading("11000")
    ) as read:
        response = api.post(f"/api/orders/{order.pk}/arrive/", {}, format="json")

    assert response.status_code == 200
    assert Decimal(response.data["weigh_in_kg"]) == Decimal("11000")
    assert response.data["weigh_in_source"] == "scale"
    read.assert_called_once_with()
    order.refresh_from_db()
    assert order.status == "arrived"
    assert order.shipment.weigh_in_source == Shipment.WeightSource.SCALE


def test_arrival_endpoint_rejects_unstable_scale_without_changing_order(
    settings, boss,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    order, _product = _order(boss)
    api = APIClient()
    api.force_authenticate(boss)

    with patch.object(
        scale, "read_truck_scale", side_effect=scale.TruckScaleNotReady()
    ):
        response = api.post(f"/api/orders/{order.pk}/arrive/", {}, format="json")

    assert response.status_code == 409
    assert response.data["code"] == "truck_scale_not_ready"
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert not Shipment.objects.filter(order=order).exists()


def test_arrival_endpoint_rejects_sample_if_number_changes_during_http_read(
    settings, boss,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    order, _product = _order(boss)
    api = APIClient()
    api.force_authenticate(boss)

    def change_number_while_reading():
        order_services.set_truck_number(order, "02ARRIVAL", boss)
        return _reading("11000")

    with patch.object(
        scale, "read_truck_scale", side_effect=change_number_while_reading
    ):
        response = api.post(f"/api/orders/{order.pk}/arrive/", {}, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "truck_number_changed_during_weighing"
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert order.truck_number == "02ARRIVAL"
    assert not Shipment.objects.filter(order=order).exists()


def test_manual_set_status_cannot_bypass_required_scale_entry(
    settings, boss,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    order, product = _order(boss)
    api = APIClient()
    api.force_authenticate(boss)

    response = api.post(
        f"/api/orders/{order.pk}/set-status/",
        {"status": "shipped", "bags_loaded": 50},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "scale_entry_weight_required"
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert not Shipment.objects.filter(order=order).exists()
    assert StockItem.objects.get(product=product).bags == 100


def test_manual_set_status_cannot_ship_new_estimated_loading(
    settings, boss, operator,
):
    order, product = _order(boss)
    record_arrival(order, None, operator)
    record_count(order, 50, operator)
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    api = APIClient()
    api.force_authenticate(boss)

    response = api.post(
        f"/api/orders/{order.pk}/set-status/",
        {"status": "shipped", "bags_loaded": 50},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "scale_entry_weight_required"
    order.refresh_from_db()
    assert order.status == "loading"
    assert order.shipment.weigh_in_source == Shipment.WeightSource.ESTIMATED
    assert StockItem.objects.get(product=product).bags == 100


def test_manual_set_status_cannot_bypass_scale_exit(
    settings, boss, operator,
):
    order, product = _loading_scale_order(settings, boss, operator)
    api = APIClient()
    api.force_authenticate(boss)

    response = api.post(
        f"/api/orders/{order.pk}/set-status/",
        {"status": "shipped", "bags_loaded": 50},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "scale_exit_weight_required"
    order.refresh_from_db()
    order.shipment.refresh_from_db()
    assert order.status == "loading"
    assert order.shipment.weigh_out_kg is None
    assert StockItem.objects.get(product=product).bags == 100


def test_entry_sample_is_rejected_if_truck_number_changes_during_http_read(
    settings, boss, operator,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    order, _product = _order(boss)

    def change_number_while_reading():
        Order.objects.filter(pk=order.pk).update(truck_number="02CHANGED")
        return _reading("10000")

    with patch.object(
        scale, "read_truck_scale", side_effect=change_number_while_reading
    ), pytest.raises(ValidationError) as exc_info:
        ensure_scale_entry_weight(order, operator)

    assert exc_info.value.detail["code"] == "truck_number_changed_during_weighing"
    assert not Shipment.objects.filter(order=order).exists()


def test_exit_sample_is_rejected_if_truck_number_changes_during_http_read(
    settings, boss, operator,
):
    order, product = _loading_scale_order(settings, boss, operator)

    def change_number_while_reading():
        Order.objects.filter(pk=order.pk).update(truck_number="02CHANGED")
        return _reading("12500")

    with patch.object(
        scale, "read_truck_scale", side_effect=change_number_while_reading
    ), pytest.raises(ValidationError) as exc_info:
        finish_loading(order, operator)

    assert exc_info.value.detail["code"] == "truck_number_changed_during_weighing"
    order.refresh_from_db()
    order.shipment.refresh_from_db()
    assert order.status == "loading"
    assert order.shipment.weigh_out_kg is None
    assert StockItem.objects.get(product=product).bags == 100


def test_confirmed_truck_number_change_invalidates_orphan_scale_entry(
    settings, boss, operator,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    order, _product = _order(boss)
    with patch.object(scale, "read_truck_scale", return_value=_reading("10000")):
        ensure_scale_entry_weight(order, operator)

    order_services.set_truck_number(order, "02OTHER", boss)

    order.refresh_from_db()
    assert order.truck_number == "02OTHER"
    assert not Shipment.objects.filter(order=order).exists()


def test_truck_number_is_locked_while_camera_reservation_is_open(
    settings, boss, operator,
):
    settings.TRUCK_SCALE_API_URL = "http://scale.test/api/v1/weight"
    order, _product = _order(boss)
    with patch.object(scale, "read_truck_scale", return_value=_reading("10000")):
        ensure_scale_entry_weight(order, operator)
    AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.STARTING,
        started_by=operator,
    )

    with pytest.raises(ValidationError) as exc_info:
        order_services.set_truck_number(order, "02OTHER", boss)

    assert exc_info.value.detail["code"] == "truck_number_locked"
    order.refresh_from_db()
    assert order.truck_number == "01SCALE"
    assert Shipment.objects.filter(order=order).exists()
