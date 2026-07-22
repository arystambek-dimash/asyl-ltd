from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from rest_framework.exceptions import ValidationError

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
