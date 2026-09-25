import json
import os

import pytest

from config.tests.settings_process import import_base_settings


def _read_settings(overrides):
    environment = {"PATH": os.environ.get("PATH", ""), "PYTEST_RUNNING": "1", **overrides}
    return import_base_settings(
        environment, "WEIGHING_AI_MODEL", "SHIPPING_WAGON_AI_MODEL", "SHIPPING_WAGON_AI_DETAIL"
    )


@pytest.mark.parametrize(
    ("weighing", "shipping", "expected"),
    [
        (None, None, "gpt-5-mini"),
        ("gpt-5.4-mini", None, "gpt-5.4-mini"),
        ("gpt-5.4-mini", "  ", "gpt-5.4-mini"),
        ("gpt-5-mini", " gpt-5.4-mini ", "gpt-5.4-mini"),
    ],
)
def test_shipping_model_override_is_independent_of_weighbridge(weighing, shipping, expected):
    environment = {}
    if weighing is not None:
        environment["WEIGHING_AI_MODEL"] = weighing
    if shipping is not None:
        environment["SHIPPING_WAGON_AI_MODEL"] = shipping
    result = _read_settings(environment)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [weighing or "gpt-5-mini", expected, "high"]


@pytest.mark.parametrize(("detail", "expected"), [("", "high"), ("high", "high"), (" original ", "original")])
def test_shipping_image_detail_accepts_only_explicit_supported_modes(detail, expected):
    result = _read_settings({"SHIPPING_WAGON_AI_DETAIL": detail})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["gpt-5-mini", "gpt-5-mini", expected]


@pytest.mark.parametrize("detail", ["auto", "low", "other", "high\noriginal"])
def test_shipping_image_detail_rejects_invalid_configuration(detail):
    result = _read_settings({"SHIPPING_WAGON_AI_DETAIL": detail})
    assert result.returncode != 0
    assert "SHIPPING_WAGON_AI_DETAIL must be high or original" in result.stderr
