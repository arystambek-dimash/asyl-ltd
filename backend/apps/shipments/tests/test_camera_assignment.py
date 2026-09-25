from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from rest_framework.exceptions import ValidationError

from apps.clients.models import Client
from apps.orders.models import Order
from apps.shipments import services

pytestmark = pytest.mark.django_db


def _integrity_error(constraint_name: str) -> IntegrityError:
    cause = RuntimeError("database constraint violation")
    cause.diag = SimpleNamespace(constraint_name=constraint_name)
    error = IntegrityError("duplicate key")
    error.__cause__ = cause
    return error


def _order():
    client = Client.objects.create_with_user(
        first_name="Camera", last_name="Busy", phone="camera-busy",
    )
    return Order.objects.create(client=client, status="confirmed")


def test_named_loading_camera_constraint_maps_to_camera_busy(operator):
    order = _order()
    error = _integrity_error(services.LOADING_CAMERA_CONSTRAINT)

    with (
        patch.object(Order, "save", side_effect=error),
        pytest.raises(ValidationError) as caught,
    ):
        services.begin_camera_loading(order, "cam3", operator)

    assert str(caught.value.detail["detail"]) == (
        "Камера уже закреплена за другим активным заказом"
    )
    assert str(caught.value.detail["code"]) == "camera_busy"


def test_unrelated_integrity_error_is_not_mapped_to_camera_busy(operator):
    order = _order()
    error = _integrity_error("some_other_constraint")

    with (
        patch.object(Order, "save", side_effect=error),
        pytest.raises(IntegrityError) as caught,
    ):
        services.begin_camera_loading(order, "cam3", operator)

    assert caught.value is error
