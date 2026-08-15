from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from rest_framework.exceptions import ValidationError

from apps.cameras.models import AiCountingSession
from apps.clients.models import Client
from apps.orders.models import Order
from apps.shipments import services


def _integrity_error(constraint_name: str) -> IntegrityError:
    cause = RuntimeError("database constraint violation")
    cause.diag = SimpleNamespace(constraint_name=constraint_name)
    error = IntegrityError("duplicate key")
    error.__cause__ = cause
    return error


def test_named_loading_camera_constraint_maps_to_camera_busy():
    error = _integrity_error(services.LOADING_CAMERA_CONSTRAINT)

    with (
        patch.object(services, "_set_loading_camera_locked", side_effect=error),
        pytest.raises(ValidationError) as caught,
    ):
        services.set_loading_camera(object(), "cam3")

    assert str(caught.value.detail["detail"]) == (
        "Камера уже закреплена за другим активным заказом"
    )
    assert str(caught.value.detail["code"]) == "camera_busy"


def test_unrelated_integrity_error_is_not_mapped_to_camera_busy():
    error = _integrity_error("some_other_constraint")

    with (
        patch.object(services, "_set_loading_camera_locked", side_effect=error),
        pytest.raises(IntegrityError) as caught,
    ):
        services.set_loading_camera(object(), "cam3")

    assert caught.value is error


@pytest.mark.django_db
def test_camera_assignment_rechecks_status_after_lock(manager):
    client = Client.objects.create_with_user(
        first_name="Camera", last_name="Race", phone="camera-race",
    )
    stale_order = Order.objects.create(client=client, status="arrived")
    Order.objects.filter(pk=stale_order.pk).update(status="loaded")

    with pytest.raises(ValidationError) as caught:
        services.set_loading_camera(stale_order, "cam3", manager)

    assert caught.value.detail["code"] == "invalid_status"
    stale_order.refresh_from_db()
    assert stale_order.status == "loaded"
    assert stale_order.loading_camera == ""


@pytest.mark.django_db
def test_manual_camera_assignment_cannot_overtake_ai_reservation(manager):
    client = Client.objects.create_with_user(
        first_name="Reserved", last_name="Camera", phone="reserved-camera",
    )
    reserved_order = Order.objects.create(client=client, status="confirmed")
    target_order = Order.objects.create(client=client, status="arrived")
    AiCountingSession.objects.create(
        order=reserved_order,
        camera="cam3",
        status=AiCountingSession.STARTING,
        started_by=manager,
    )

    with pytest.raises(ValidationError) as caught:
        services.set_loading_camera(target_order, "cam3", manager)

    assert caught.value.detail["code"] == "camera_busy"
    target_order.refresh_from_db()
    assert target_order.loading_camera == ""
