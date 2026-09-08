"""Physical occupancy cannot be inferred from OCR or worker heartbeat age."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.cameras import ai
from apps.cameras.shipping_tracking import current_tracking, validate_tracking


def body_tracking(now, **overrides):
    value = {
        "schema_version": 1,
        "basis": "transport_body",
        "presence": "present",
        "motion": "stationary",
        "visit_id": "camera-generation:visit-1",
        "observed_at": now.isoformat(),
        "present_since": (now - timedelta(seconds=6)).isoformat(),
        "last_seen_at": now.isoformat(),
        "stationary_since": (now - timedelta(seconds=4)).isoformat(),
        "absent_since": None,
        "detection_count": 1,
        "reason": "observed",
        "number_associated": True,
    }
    return {**value, **overrides}


def test_confirmed_body_motion_and_capture_time_are_preserved():
    now = timezone.now()
    value = body_tracking(now)
    assert validate_tracking(value, now) == value


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"basis": "number_region"},
        {"presence": "maybe"},
        {"motion": "stopped"},
        {"detection_count": True},
        {"detection_count": 0},
        {"detection_count": 2},
        {"visit_id": ""},
        {"visit_id": "x" * 257},
        {"number_associated": "true"},
        {"reason": "x" * 129},
        {"present_since": None},
        {"last_seen_at": None},
        {"stationary_since": None},
    ],
)
def test_invalid_presence_never_authorizes_acquisition(changes):
    now = timezone.now()
    with pytest.raises(ai.AiProtocolError):
        validate_tracking(body_tracking(now, **changes), now)


@pytest.mark.parametrize("field", ["observed_at", "last_seen_at", "present_since", "stationary_since"])
def test_future_or_mismatching_observation_cannot_claim_current_transport(field):
    now = timezone.now()
    with pytest.raises(ai.AiProtocolError):
        validate_tracking(body_tracking(now, **{field: (now + timedelta(seconds=1)).isoformat()}), now)


@pytest.mark.parametrize("presence,span", [("present", 1.9), ("absent", 5.9)])
def test_one_frame_or_a_short_run_is_not_confirmed_presence_or_absence(presence, span):
    now = timezone.now()
    value = body_tracking(now, presence=presence, motion="unknown", number_associated=False)
    value[f"{presence}_since"] = (now - timedelta(seconds=span)).isoformat()
    if presence == "absent":
        value["detection_count"] = 0
    with pytest.raises(ai.AiProtocolError):
        validate_tracking(value, now)


def test_confirmed_absence_is_separate_from_a_missing_number():
    now = timezone.now()
    value = body_tracking(
        now, presence="absent", motion="unknown", number_associated=False,
        detection_count=0, absent_since=(now - timedelta(seconds=6)).isoformat(),
    )
    assert validate_tracking(value, now)["presence"] == "absent"
    assert validate_tracking(None, now)["presence"] == "unknown"


def test_present_body_does_not_require_readable_ocr():
    now = timezone.now()
    value = validate_tracking(body_tracking(now, number_associated=False), now)
    assert value["presence"] == "present"
    assert value["motion"] == "stationary"
    assert value["number_associated"] is False


def test_stale_frame_is_unknown_even_if_worker_is_still_polling(monkeypatch):
    now = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: now)
    value = body_tracking(now - timedelta(seconds=16))
    assert current_tracking(value)["presence"] == "unknown"
    assert current_tracking(value)["reason"] == "stale_frame"
    assert current_tracking(body_tracking(now))["presence"] == "present"


def test_unknown_or_absent_cannot_have_motion_or_associated_plate():
    now = timezone.now()
    for presence in ("unknown", "absent"):
        with pytest.raises(ai.AiProtocolError):
            validate_tracking(body_tracking(now, presence=presence), now)
