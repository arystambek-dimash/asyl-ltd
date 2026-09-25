import http.client
import json
from io import BytesIO
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import pytest
from apps.grain import scale

READY = {
    "weight_kg": 12_345.67,
    "stable": True,
    "connected": True,
    "stale": False,
    "age_seconds": 0.4,
    "updated_at": "2026-08-10T10:20:30+05:00",
    "error": None,
}
# What the real weighbridge reports while nothing stands on it.
EMPTY_SCALE_PAYLOAD = {
    "weight_kg": None,
    "stable": False,
    "gross": None,
    "connected": True,
    "stale": True,
    "age_seconds": None,
    "updated_at": None,
    "raw": "",
    "port": "COM11",
    "baud": 9600,
    "error": None,
}
MISSING = object()
MALFORMED_FIELDS = [
    *(
        (field, value)
        for field in ("connected", "stable", "stale")
        for value in (None, 0, 1, "true")
    ),
    *(("error", value) for value in (0, False, [], {})),
    *(
        (field, value)
        for field in ("weight_kg", "age_seconds")
        for value in (None, True, "12.5", [], {})
    ),
    ("age_seconds", -0.01),
    *(("updated_at", value) for value in (0, False, [], {})),
    *(
        (field, MISSING)
        for field in ("connected", "stable", "stale", "error", "updated_at")
    ),
]


class UpstreamResponse(BytesIO):
    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


def response(payload=READY, *, status=200):
    return UpstreamResponse(json.dumps(payload).encode(), status=status)


def open_patch(*, return_value=None, side_effect=None):
    return patch.object(
        scale,
        "open_local_request",
        return_value=return_value,
        side_effect=side_effect,
    )


@pytest.fixture(autouse=True)
def scale_settings(settings):
    settings.WAGON_SCALE_API_URL = "http://wagon-scale.test/api/v1/weight"
    settings.TRUCK_SCALE_API_URL = "http://truck-scale.test/api/v1/weight"
    settings.TRUCK_SCALE_TIMEOUT_SECONDS = 1.25
    settings.TRUCK_SCALE_PREVIEW_TIMEOUT_SECONDS = 0.75
    settings.TRUCK_SCALE_MAX_AGE_SECONDS = 5
    settings.TRUCK_SCALE_MAX_WEIGHT_KG = 100_000


def assert_error(exc_info, *, status, code):
    assert exc_info.value.status_code == status
    assert exc_info.value.get_codes() == code
    assert str(exc_info.value.detail)


def test_each_scale_key_reads_only_its_configured_url(settings):
    wagon_response = response({**READY, "weight_kg": 68_000})
    truck_response = response({**READY, "weight_kg": 12_000})

    with open_patch(
        side_effect=[wagon_response, truck_response]
    ) as request_open:
        wagon = scale.read_truck_scale(scale.WAGON_SCALE_KEY)
        truck = scale.read_truck_scale(scale.TRUCK_SCALE_KEY)

    assert wagon.weight_kg == scale.Decimal("68000.00")
    assert truck.weight_kg == scale.Decimal("12000.00")
    assert [
        item.args[0].full_url for item in request_open.call_args_list
    ] == [
        settings.WAGON_SCALE_API_URL,
        settings.TRUCK_SCALE_API_URL,
    ]


@pytest.mark.parametrize("url", ["", "   "])
def test_missing_truck_scale_fails_closed_without_using_wagon_scale(
    settings, url
):
    settings.TRUCK_SCALE_API_URL = url

    with open_patch() as request_open, pytest.raises(
        scale.TruckScaleDisabled
    ) as exc_info:
        scale.read_truck_scale(scale.TRUCK_SCALE_KEY)

    request_open.assert_not_called()
    assert_error(exc_info, status=503, code="truck_scale_disabled")


def test_missing_wagon_scale_fails_closed_without_using_truck_scale(settings):
    settings.WAGON_SCALE_API_URL = ""

    with open_patch() as request_open, pytest.raises(
        scale.TruckScaleDisabled
    ) as exc_info:
        scale.read_truck_scale(scale.WAGON_SCALE_KEY)

    request_open.assert_not_called()
    assert_error(exc_info, status=503, code="truck_scale_disabled")


def test_unknown_scale_key_is_rejected_before_io():
    with open_patch() as request_open, pytest.raises(ValueError):
        scale.read_truck_scale("unknown")

    request_open.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://scale.test/api/v1/weight",
        "file:///etc/passwd",
        "//scale.test/api/v1/weight",
        "http:///api/v1/weight",
        "http://user:password@scale.test/api/v1/weight",
        "http://user@scale.test/api/v1/weight",
        "http://scale.test/api/v1/weight#fragment",
        "http://scale.test/api/v1/weight#",
        "http://scale.test:invalid/api/v1/weight",
        "http://[::1/api/v1/weight",
    ],
)
def test_unsafe_or_malformed_configured_url_is_rejected_before_io(settings, url):
    settings.TRUCK_SCALE_API_URL = url
    with open_patch() as request_open, \
         pytest.raises(scale.TruckScaleUnavailable) as exc_info:
        scale.read_truck_scale()

    request_open.assert_not_called()
    assert_error(exc_info, status=503, code="truck_scale_unreachable")


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_http_and_https_urls_are_accepted(settings, scheme):
    settings.TRUCK_SCALE_API_URL = (
        f"{scheme}://scale.test/api/v1/weight?site=1"
    )
    upstream = response()
    with open_patch(return_value=upstream) as request_open:
        scale.read_truck_scale()

    assert request_open.call_args.args[0].full_url == settings.TRUCK_SCALE_API_URL


def test_secure_opener_disables_environment_proxies_and_redirects():
    upstream = response()
    opener = Mock()
    opener.open.return_value = upstream

    with patch.object(
        scale.urllib.request, "build_opener", return_value=opener
    ) as build_opener:
        reading = scale.read_truck_scale()

    assert reading.weight_kg == scale.Decimal("12345.67")
    opener.open.assert_called_once()
    assert opener.open.call_args.kwargs == {"timeout": 1.25}
    proxy_handler, redirect_handler = build_opener.call_args.args
    assert isinstance(proxy_handler, scale.urllib.request.ProxyHandler)
    assert proxy_handler.proxies == {}
    assert isinstance(redirect_handler, scale._NoRedirectHandler)

    original = scale.urllib.request.Request("http://scale.test/api/v1/weight")
    assert redirect_handler.redirect_request(
        original, None, 302, "Found", {}, "http://scale.test/other"
    ) is None
    assert redirect_handler.redirect_request(
        original, None, 302, "Found", {}, "http://attacker.test/weight"
    ) is None
    assert upstream.closed


def test_redirect_response_is_unavailable_and_never_retried():
    redirect = HTTPError(
        "http://scale.test/api/v1/weight",
        302,
        "Found",
        {"Location": "http://attacker.test/weight"},
        BytesIO(b"redirect"),
    )
    with open_patch(side_effect=redirect) as request_open, \
         pytest.raises(scale.TruckScaleUnavailable) as exc_info:
        scale.read_truck_scale()

    request_open.assert_called_once()
    assert redirect.closed
    assert_error(exc_info, status=503, code="truck_scale_unreachable")


def test_success_returns_exact_decimals_and_uses_bounded_get_request():
    upstream = response()
    with open_patch(return_value=upstream) as urlopen:
        reading = scale.read_truck_scale()

    assert reading == scale.ScaleReading(
        weight_kg=scale.Decimal("12345.67"),
        age_seconds=scale.Decimal("0.4"),
        updated_at="2026-08-10T10:20:30+05:00",
    )
    request = urlopen.call_args.args[0]
    assert request.full_url == "http://truck-scale.test/api/v1/weight"
    assert request.get_method() == "GET"
    assert request.get_header("Accept") == "application/json"
    assert urlopen.call_args.kwargs == {"timeout": 1.25}
    assert upstream.read_sizes == [scale.MAX_RESPONSE_BYTES + 1]
    assert upstream.closed


def test_updated_at_may_be_null():
    upstream = response({**READY, "updated_at": None})
    with open_patch(return_value=upstream):
        reading = scale.read_truck_scale()

    assert reading.updated_at is None


def test_exact_response_limit_is_accepted():
    encoded = json.dumps(READY).encode()
    upstream = UpstreamResponse(encoded + b" " * (scale.MAX_RESPONSE_BYTES - len(encoded)))
    with open_patch(return_value=upstream):
        reading = scale.read_truck_scale()

    assert reading.weight_kg == scale.Decimal("12345.67")
    assert upstream.closed


def test_oversized_response_is_rejected_and_closed():
    upstream = UpstreamResponse(b" " * (scale.MAX_RESPONSE_BYTES + 1))
    with open_patch(return_value=upstream), \
         pytest.raises(scale.TruckScaleMalformedResponse) as exc_info:
        scale.read_truck_scale()

    assert upstream.read_sizes == [scale.MAX_RESPONSE_BYTES + 1]
    assert upstream.closed
    assert_error(exc_info, status=502, code="truck_scale_malformed_response")


@pytest.mark.parametrize(
    "body",
    [b"", b"{", b"[]", b"null", b'"value"', b"\xff", b'{"weight_kg":NaN}'],
)
def test_malformed_json_or_non_object_is_rejected(body):
    upstream = UpstreamResponse(body)
    with open_patch(return_value=upstream), \
         pytest.raises(scale.TruckScaleMalformedResponse) as exc_info:
        scale.read_truck_scale()

    assert upstream.closed
    assert_error(exc_info, status=502, code="truck_scale_malformed_response")


@pytest.mark.parametrize(
    "upstream_error",
    [
        URLError("no route"),
        TimeoutError("timed out"),
        OSError("connection reset"),
        http.client.HTTPException("broken response"),
    ],
)
def test_network_failure_is_unreachable_and_is_not_retried(upstream_error):
    with open_patch(side_effect=upstream_error) as urlopen, pytest.raises(scale.TruckScaleUnavailable) as exc_info:
        scale.read_truck_scale()

    assert urlopen.call_count == 1
    assert_error(exc_info, status=503, code="truck_scale_unreachable")


def test_http_error_is_closed_and_reported_as_unreachable():
    error = HTTPError(
        "http://scale.test/api/v1/weight", 500, "failure", {}, BytesIO(b"failure")
    )
    with open_patch(side_effect=error), \
         pytest.raises(scale.TruckScaleUnavailable) as exc_info:
        scale.read_truck_scale()

    assert error.closed
    assert_error(exc_info, status=503, code="truck_scale_unreachable")


def test_unexpected_http_status_is_closed_and_reported_as_unreachable():
    upstream = response(status=204)
    with open_patch(return_value=upstream), \
         pytest.raises(scale.TruckScaleUnavailable) as exc_info:
        scale.read_truck_scale()

    assert upstream.closed
    assert_error(exc_info, status=503, code="truck_scale_unreachable")


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({**READY, "connected": False}, id="disconnected"),
        pytest.param({**READY, "stable": False}, id="unstable"),
        pytest.param({**READY, "stale": True}, id="stale"),
        pytest.param({**READY, "error": "COM11 disconnected"}, id="error"),
        pytest.param(EMPTY_SCALE_PAYLOAD, id="real-empty-scale"),
        pytest.param({**READY, "weight_kg": 0}, id="zero-weight"),
        pytest.param({**READY, "weight_kg": -1}, id="negative-weight"),
        pytest.param({**READY, "weight_kg": 100_000.01}, id="over-limit"),
        pytest.param({**READY, "weight_kg": 0.004}, id="rounds-to-zero"),
        pytest.param({**READY, "age_seconds": 5.01}, id="too-old"),
    ],
)
def test_well_formed_but_not_ready_payload_returns_conflict(payload):
    upstream = response(payload)
    with open_patch(return_value=upstream), \
         pytest.raises(scale.TruckScaleNotReady) as exc_info:
        scale.read_truck_scale()

    assert_error(exc_info, status=409, code="truck_scale_not_ready")


@pytest.mark.parametrize(
    ("payload", "state", "weight", "stale"),
    [
        (READY, "ready", "12345.67", False),
        ({**READY, "stable": False}, "unstable", "12345.67", False),
        ({**READY, "stale": True}, "stale", None, True),
        ({**READY, "connected": False}, "disconnected", None, False),
        ({**READY, "error": "COM11 disconnected"}, "unavailable", None, False),
        (
            {**READY, "stable": False, "age_seconds": 5.01},
            "stale",
            None,
            True,
        ),
    ],
)
def test_observation_maps_display_states_and_hides_unsafe_weight(
    payload, state, weight, stale
):
    upstream = response(payload)
    with open_patch(return_value=upstream) as request_open:
        observation = scale.read_truck_scale_observation()

    assert observation.state == state
    assert (
        str(observation.weight_kg)
        if observation.weight_kg is not None
        else None
    ) == weight
    assert observation.stale is stale
    assert request_open.call_args.kwargs == {"timeout": 0.75}


def test_real_empty_scale_payload_is_a_stale_observation_without_a_weight():
    upstream = response(EMPTY_SCALE_PAYLOAD)
    with open_patch(return_value=upstream):
        observation = scale.read_truck_scale_observation()

    assert observation == scale.ScaleObservation(
        state="stale",
        weight_kg=None,
        connected=True,
        stable=False,
        stale=True,
        age_seconds=None,
        updated_at=None,
    )


def test_zero_is_displayable_but_authoritative_capture_still_rejects_it():
    zero = {**READY, "weight_kg": 0}
    with open_patch(side_effect=[response(zero), response(zero)]) as request_open:
        observation = scale.read_truck_scale_observation()
        with pytest.raises(scale.TruckScaleNotReady):
            scale.read_truck_scale()

    assert observation.state == "ready"
    assert observation.weight_kg == scale.Decimal("0.00")
    assert [call.kwargs["timeout"] for call in request_open.call_args_list] == [
        0.75,
        1.25,
    ]


@pytest.mark.parametrize(("field", "value"), MALFORMED_FIELDS)
def test_malformed_field_is_rejected(field, value):
    payload = READY.copy()
    if value is MISSING:
        payload.pop(field)
    else:
        payload[field] = value
    upstream = response(payload)
    with open_patch(return_value=upstream), \
         pytest.raises(scale.TruckScaleMalformedResponse) as exc_info:
        scale.read_truck_scale()

    assert_error(exc_info, status=502, code="truck_scale_malformed_response")


def test_weight_at_configured_limit_is_accepted():
    upstream = response({**READY, "weight_kg": 100_000})
    with open_patch(return_value=upstream):
        reading = scale.read_truck_scale()

    assert reading.weight_kg == scale.Decimal("100000")
    assert reading.weight_kg.as_tuple().exponent == -2


@pytest.mark.parametrize(
    ("weight", "expected"),
    [
        (12_345.674, "12345.67"),
        (12_345.675, "12345.68"),
        (12_345.684, "12345.68"),
        (12_345.685, "12345.69"),
    ],
)
def test_weight_is_normalized_to_model_precision_with_half_up(weight, expected):
    upstream = response({**READY, "weight_kg": weight})
    with open_patch(return_value=upstream):
        reading = scale.read_truck_scale()

    assert reading.weight_kg == scale.Decimal(expected)
    assert reading.weight_kg.as_tuple().exponent == -2


def test_two_normalized_readings_produce_an_exact_two_decimal_net():
    weigh_in = response({**READY, "weight_kg": 12_500.005})
    weigh_out = response({**READY, "weight_kg": 18_000.005})
    with open_patch(side_effect=[weigh_in, weigh_out]):
        first = scale.read_truck_scale()
        second = scale.read_truck_scale()

    assert first.weight_kg == scale.Decimal("12500.01")
    assert second.weight_kg == scale.Decimal("18000.01")
    assert second.weight_kg - first.weight_kg == scale.Decimal("5500.00")


def test_age_at_configured_limit_is_accepted():
    upstream = response({**READY, "age_seconds": 5})
    with open_patch(return_value=upstream):
        reading = scale.read_truck_scale()

    assert reading.age_seconds == scale.Decimal("5")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TRUCK_SCALE_TIMEOUT_SECONDS", 0),
        ("TRUCK_SCALE_TIMEOUT_SECONDS", float("nan")),
        ("TRUCK_SCALE_MAX_AGE_SECONDS", -1),
        ("TRUCK_SCALE_MAX_WEIGHT_KG", 0),
        ("TRUCK_SCALE_MAX_WEIGHT_KG", "invalid"),
    ],
)
def test_invalid_runtime_configuration_fails_closed(settings, name, value):
    setattr(settings, name, value)
    upstream = response()
    with open_patch(return_value=upstream), \
         pytest.raises(scale.TruckScaleUnavailable) as exc_info:
        scale.read_truck_scale()

    assert_error(exc_info, status=503, code="truck_scale_unreachable")


@pytest.mark.parametrize("value", [0, -1, float("nan"), "invalid"])
def test_invalid_preview_timeout_fails_closed_before_io(settings, value):
    settings.TRUCK_SCALE_PREVIEW_TIMEOUT_SECONDS = value

    with open_patch() as request_open, \
         pytest.raises(scale.TruckScaleUnavailable) as exc_info:
        scale.read_truck_scale_observation()

    request_open.assert_not_called()
    assert_error(exc_info, status=503, code="truck_scale_unreachable")
