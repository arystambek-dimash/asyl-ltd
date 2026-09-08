"""Validate physical transport observations independently of plate OCR."""

from collections.abc import Mapping
from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import ai

TIME_FIELDS = (
    "present_since", "last_seen_at", "stationary_since", "absent_since",
)


def unknown_tracking(reason="tracking_unavailable", *, observed_at=None):
    return {
        "schema_version": 1,
        "basis": "transport_body",
        "presence": "unknown",
        "motion": "unknown",
        "visit_id": None,
        "observed_at": observed_at.isoformat() if observed_at else None,
        **{field: None for field in TIME_FIELDS},
        "detection_count": 0,
        "reason": reason,
        "number_associated": False,
    }


def _time(raw):
    try:
        value = parse_datetime(raw) if isinstance(raw, str) else None
    except ValueError:
        value = None
    if value is None or timezone.is_naive(value):
        raise ValueError("invalid tracking timestamp")
    return value


def validate_tracking(raw, observed_at):
    """A legacy number-only CV reply cannot silently authorize acquisition."""
    if raw is None:
        return unknown_tracking()
    try:
        if (
            not isinstance(raw, Mapping)
            or type(raw.get("schema_version")) is not int
            or raw["schema_version"] != 1
            or raw.get("basis") != "transport_body"
            or raw.get("presence") not in ("present", "absent", "unknown")
            or raw.get("motion") not in ("stationary", "moving", "unknown")
            or type(raw.get("number_associated")) is not bool
            or type(raw.get("detection_count")) is not int
            or not 0 <= raw["detection_count"] <= 1024
            or not isinstance(raw.get("reason"), str)
            or not 1 <= len(raw["reason"]) <= 128
            or _time(raw.get("observed_at")) != observed_at
        ):
            raise ValueError("invalid tracking contract")
        visit = raw.get("visit_id")
        if visit is not None and (
            not isinstance(visit, str) or not 1 <= len(visit) <= 256
        ):
            raise ValueError("invalid visit")
        moments = {}
        for field in TIME_FIELDS:
            if field not in raw:
                raise ValueError("missing tracking timestamp")
            moments[field] = _time(raw[field]) if raw[field] is not None else None
            if moments[field] and moments[field] > observed_at:
                raise ValueError("future tracking timestamp")
        present = raw["presence"] == "present"
        if not present and (
            raw["motion"] != "unknown" or raw["number_associated"]
        ):
            raise ValueError("unobserved transport cannot be stationary or own a plate")
        if present and (
            not visit
            or raw["detection_count"] != 1
            or moments["present_since"] is None
            or moments["last_seen_at"] != observed_at
            or observed_at - moments["present_since"] < timedelta(seconds=2)
            or moments["absent_since"] is not None
        ):
            raise ValueError("unconfirmed presence")
        if raw["motion"] == "stationary" and (
            moments["stationary_since"] is None
            or observed_at - moments["stationary_since"] < timedelta(seconds=4)
        ):
            raise ValueError("unconfirmed stationary transport")
        if raw["presence"] == "absent" and (
            raw["detection_count"] != 0
            or moments["absent_since"] is None
            or observed_at - moments["absent_since"] < timedelta(seconds=6)
        ):
            raise ValueError("unconfirmed absence")
    except (TypeError, ValueError) as exc:
        raise ai.AiProtocolError(
            "ПК камер вернул некорректное состояние транспорта"
        ) from exc
    return {
        "schema_version": 1,
        "basis": "transport_body",
        **{key: raw[key] for key in (
            "presence", "motion", "observed_at", "detection_count", "reason",
            "number_associated",
        )},
        "visit_id": visit,
        **{key: raw[key] for key in TIME_FIELDS},
    }


def current_tracking(raw):
    """Read-side freshness uses the actual body frame, not a worker heartbeat."""
    try:
        observed = _time(raw.get("observed_at"))
        value = validate_tracking(raw, observed)
        now = timezone.now()
        if not now - timedelta(seconds=15) <= observed <= now + timedelta(seconds=5):
            return unknown_tracking("stale_frame")
        return value
    except (AttributeError, ValueError, ai.AiProtocolError):
        return unknown_tracking()
