"""Validated camera journal DTOs and classification normalization; no I/O."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import ANALYTICS_SCOPE_AI247, ANALYTICS_SCOPE_SHIPPING

EVENT_PAGE_LIMIT = 500


class EventSyncError(Exception):
    """The upstream journal cannot be advanced without risking lost counts."""


@dataclass(frozen=True)
class CountEvent:
    upstream_event_id: int
    occurred_at: datetime
    camera: str
    source: str
    mode: str
    continuous_analytics: bool
    analytics_scope: str
    class_name: str
    total_after: int
    color: str | None = None
    color_confidence: float | None = None
    brand: str | None = None
    brand_confidence: float | None = None
    sku: str | None = None
    classification_status: str | None = None


@dataclass(frozen=True)
class EventPage:
    events: tuple[CountEvent, ...]
    next_after_id: int
    has_more: bool
    enrichment_pending: bool
    journal_id: str | None


def _plain_int(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EventSyncError(f"AI /events: invalid {field}")
    return value


def _optional_text(
    raw: dict,
    field: str,
    *,
    max_length: int,
) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise EventSyncError(f"AI /events: invalid event.{field}")
    return value


def _optional_confidence(raw: dict, field: str) -> float | None:
    value = raw.get(field)
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise EventSyncError(f"AI /events: invalid event.{field}")
    return float(value)


def _parse_event(raw: object, *, camera: str, previous_id: int) -> CountEvent:
    if not isinstance(raw, dict):
        raise EventSyncError("AI /events: event must be an object")
    event_id = _plain_int(raw.get("id"), "event.id", minimum=1)
    if event_id <= previous_id:
        raise EventSyncError("AI /events: event ids must be strictly increasing")
    if raw.get("cam") != camera:
        raise EventSyncError("AI /events: event camera does not match the filter")

    created_at = raw.get("created_at")
    occurred_at = parse_datetime(created_at) if isinstance(created_at, str) else None
    if occurred_at is None or timezone.is_naive(occurred_at):
        raise EventSyncError("AI /events: invalid event.created_at")

    mode = raw.get("mode")
    if mode not in {"always_on", "session"}:
        raise EventSyncError("AI /events: invalid event.mode")
    continuous_analytics = raw.get("continuous_analytics", False)
    if not isinstance(continuous_analytics, bool):
        raise EventSyncError("AI /events: invalid event.continuous_analytics")
    analytics_scope = raw.get("analytics_scope")
    if analytics_scope not in {ANALYTICS_SCOPE_SHIPPING, ANALYTICS_SCOPE_AI247}:
        raise EventSyncError("AI /events: invalid event.analytics_scope")
    source = raw.get("source")
    if source not in {"main", "sub"}:
        raise EventSyncError("AI /events: invalid event.source")
    if source != "sub" and (mode == "always_on" or continuous_analytics):
        raise EventSyncError(
            "AI /events: continuous analytics event must use sub source"
        )
    class_name = raw.get("class_name")
    if not isinstance(class_name, str) or len(class_name) > 100:
        raise EventSyncError("AI /events: invalid event.class_name")
    total_after = _plain_int(raw.get("total_after"), "event.total_after")
    return CountEvent(
        upstream_event_id=event_id,
        occurred_at=occurred_at,
        camera=camera,
        source=source,
        mode=mode,
        continuous_analytics=continuous_analytics,
        analytics_scope=analytics_scope,
        class_name=class_name,
        total_after=total_after,
        color=_optional_text(raw, "color", max_length=100),
        color_confidence=_optional_confidence(raw, "color_confidence"),
        brand=_optional_text(raw, "brand", max_length=100),
        brand_confidence=_optional_confidence(raw, "brand_confidence"),
        sku=_optional_text(raw, "sku", max_length=255),
        classification_status=_optional_text(
            raw,
            "classification_status",
            max_length=32,
        ),
    )


def _applies_to_continuous_analytics(event: CountEvent) -> bool:
    """Honor the durable decision made when the camera event was created."""

    return event.mode == "always_on" or (
        event.mode == "session" and event.continuous_analytics
    )


def parse_page(payload: object, *, camera: str, after_id: int) -> EventPage:
    """Validate the observed production /events contract without coercion."""

    if not isinstance(payload, dict):
        raise EventSyncError("AI /events: response must be an object")
    raw_events = payload.get("events")
    if not isinstance(raw_events, list) or len(raw_events) > EVENT_PAGE_LIMIT:
        raise EventSyncError("AI /events: invalid events page")
    has_more = payload.get("has_more")
    if not isinstance(has_more, bool):
        raise EventSyncError("AI /events: invalid has_more")
    enrichment_pending = payload.get("enrichment_pending", False)
    if not isinstance(enrichment_pending, bool):
        raise EventSyncError("AI /events: invalid enrichment_pending")
    journal_id = payload.get("journal_id")
    if journal_id is not None and (
        not isinstance(journal_id, str)
        or not journal_id.strip()
        or len(journal_id) > 64
    ):
        raise EventSyncError("AI /events: invalid journal_id")

    events: list[CountEvent] = []
    previous_id = after_id
    for raw in raw_events:
        event = _parse_event(raw, camera=camera, previous_id=previous_id)
        events.append(event)
        previous_id = event.upstream_event_id

    next_after_id = _plain_int(payload.get("next_after_id"), "next_after_id")
    expected_next = events[-1].upstream_event_id if events else after_id
    if next_after_id != expected_next:
        raise EventSyncError("AI /events: next_after_id skipped an event")
    if has_more and not events:
        raise EventSyncError("AI /events: has_more without cursor progress")
    return EventPage(
        tuple(events),
        next_after_id,
        has_more,
        enrichment_pending,
        journal_id,
    )


def event_color_key(color: str | None, class_name: str | None) -> str:
    """Base colour of one counted bag, or "" when the camera did not classify it."""
    key = (color or class_name or "").split("_", 1)[0].strip().lower()
    return key if len(key) <= 32 else ""


def _event_color(event: CountEvent) -> dict[str, int]:
    color = event_color_key(event.color, event.class_name)
    return {color: 1} if color else {}


def _event_brand(event: CountEvent) -> dict[str, int] | None:
    """Return a classified brand, preserving absence as legacy data."""

    if event.brand is None:
        return None
    brand = " ".join(event.brand.split()).lower()
    return {brand: 1} if brand and len(brand) <= 100 else None
