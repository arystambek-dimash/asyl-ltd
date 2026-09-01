"""Strict, checksum-pinned import of manually recovered bag analytics.

The recovery ledger is deliberately isolated from the camera-PC event journal.
Applying a file only adds its aggregate to unarchived daily chart rows and
writes immutable audit records; it cannot affect warehouse production, stock,
or the live camera cursor.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import (
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    ManualBagAnalyticsImportBatch,
    ManualBagAnalyticsImportEvent,
)

SCHEMA_NAME = "asyl.best_pt_manual_bag_events.v1"
ANALYTICS_SCOPE = "ai_247"
MODEL_ID = "best.pt"
MAX_FILE_BYTES = 256 * 1024 * 1024
# Keep every validation query below PostgreSQL's bind-parameter ceiling.
MAX_EVENTS = 50_000
ALMATY = ZoneInfo("Asia/Almaty")
CAMERA_RE = re.compile(r"^cam[1-9]\d*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ManualAnalyticsImportError(Exception):
    """The recovery file or current database state is unsafe to import."""


@dataclass(frozen=True)
class RecoveredEvent:
    idempotency_key: str
    sequence: int
    captured_at: datetime
    local_day: date
    camera: str
    source: str
    model_event_origin: str
    source_row_id: int
    shadow_run_id: int | None
    class_name: str
    color: str
    normalized_color: str
    color_confidence: float | None
    brand: str | None
    normalized_brand: str
    brand_confidence: float | None
    sku: str | None
    classification_status: str


@dataclass(frozen=True)
class ManualAnalyticsDocument:
    path: Path
    file_sha256: str
    schema_name: str
    model_id: str
    model_sha256: str
    camera: str
    source: str
    analytics_scope: str
    events: tuple[RecoveredEvent, ...]
    per_day: dict[str, dict[str, Any]]

    @property
    def first_captured_at(self) -> datetime:
        return self.events[0].captured_at

    @property
    def last_captured_at(self) -> datetime:
        return self.events[-1].captured_at


@dataclass(frozen=True)
class ManualAnalyticsImportResult:
    status: str
    batch_id: int | None


def _fail(message: str) -> ManualAnalyticsImportError:
    return ManualAnalyticsImportError(message)


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(f"{field} must be an object")
    return value


def _text(
    value: object,
    field: str,
    *,
    max_length: int,
    allowed: set[str] | None = None,
) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise _fail(f"{field} must be a non-empty string up to {max_length} chars")
    if allowed is not None and value not in allowed:
        raise _fail(f"{field} has an unsupported value")
    return value


def _optional_text(value: object, field: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    return _text(value, field, max_length=max_length)


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _fail(f"{field} must be an integer >= {minimum}")
    return value


def _number(value: object, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise _fail(f"{field} must be a finite number")
    return result


def _confidence(value: object, field: str, *, optional: bool = False) -> float | None:
    if optional and value is None:
        return None
    result = _number(value, field)
    if not 0 <= result <= 1:
        raise _fail(f"{field} must be between 0 and 1")
    return result


def _aware_datetime(value: object, field: str) -> datetime:
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or timezone.is_naive(parsed):
        raise _fail(f"{field} must be an ISO-8601 datetime with an offset")
    return parsed


def _normalized_color(value: str, field: str) -> str:
    color = value.split("_", 1)[0].strip().lower()
    if not color or len(color) > 32:
        raise _fail(f"{field} does not contain a valid base color")
    return color


def _normalized_brand(value: str | None) -> str:
    if value is None:
        return "unclassified"
    brand = " ".join(value.split()).lower()
    if not brand or len(brand) > 100:
        raise _fail("event.brand does not contain a valid brand")
    return brand


def _validate_event(
    raw_value: object,
    *,
    index: int,
    camera: str,
    source: str,
    model_sha256: str,
    threshold: float,
) -> RecoveredEvent:
    raw = _object(raw_value, f"events[{index - 1}]")
    prefix = f"events[{index - 1}]"
    sequence = _integer(raw.get("sequence"), f"{prefix}.sequence", minimum=1)
    if sequence != index:
        raise _fail("event.sequence must be contiguous and start at 1")

    captured_at = _aware_datetime(raw.get("captured_at"), f"{prefix}.captured_at")
    local_day = captured_at.astimezone(ALMATY).date()
    if raw.get("local_date") != local_day.isoformat():
        raise _fail(f"{prefix}.local_date does not match Asia/Almaty")
    if raw.get("camera") != camera or raw.get("source") != source:
        raise _fail(f"{prefix} camera/source does not match coverage")
    if raw.get("analytics_scope") != ANALYTICS_SCOPE:
        raise _fail(f"{prefix}.analytics_scope must be {ANALYTICS_SCOPE}")
    if raw.get("mode") != "always_on":
        raise _fail(f"{prefix}.mode must be always_on")
    if raw.get("model_id") != MODEL_ID or raw.get("model_sha256") != model_sha256:
        raise _fail(f"{prefix} model identity does not match file metadata")

    origin = _text(
        raw.get("model_event_origin"),
        f"{prefix}.model_event_origin",
        max_length=32,
        allowed={"shadow_candidate", "production"},
    )
    source_row_id = _integer(
        raw.get("source_row_id"), f"{prefix}.source_row_id", minimum=1
    )
    shadow_run_id: int | None
    if origin == "shadow_candidate":
        shadow_run_id = _integer(
            raw.get("shadow_run_id"), f"{prefix}.shadow_run_id", minimum=1
        )
        expected_key = (
            f"bestpt:{model_sha256}:shadow:{shadow_run_id}:{source_row_id}"
        )
    else:
        if raw.get("shadow_run_id") is not None:
            raise _fail(f"{prefix}.shadow_run_id must be null for production")
        shadow_run_id = None
        expected_key = f"bestpt:{model_sha256}:production:{source_row_id}"
    idempotency_key = _text(
        raw.get("idempotency_key"),
        f"{prefix}.idempotency_key",
        max_length=255,
    )
    if idempotency_key != expected_key:
        raise _fail(f"{prefix}.idempotency_key does not match its provenance")

    _integer(raw.get("frame"), f"{prefix}.frame")
    _integer(raw.get("track_id"), f"{prefix}.track_id", minimum=1)
    _integer(raw.get("class_id"), f"{prefix}.class_id")
    class_name = _text(raw.get("class_name"), f"{prefix}.class_name", max_length=100)
    confidence = _confidence(raw.get("confidence"), f"{prefix}.confidence")
    if confidence is None or confidence < threshold:
        raise _fail(f"{prefix}.confidence is below the file threshold")
    _text(raw.get("direction"), f"{prefix}.direction", max_length=16)
    _number(raw.get("point_x"), f"{prefix}.point_x")
    _number(raw.get("point_y"), f"{prefix}.point_y")
    _number(raw.get("weight_kg"), f"{prefix}.weight_kg", minimum=0.000001)

    color = _text(raw.get("color"), f"{prefix}.color", max_length=100)
    color_confidence = _confidence(
        raw.get("color_confidence"),
        f"{prefix}.color_confidence",
        optional=True,
    )
    brand = _optional_text(raw.get("brand"), f"{prefix}.brand", max_length=100)
    brand_confidence = _confidence(
        raw.get("brand_confidence"),
        f"{prefix}.brand_confidence",
        optional=True,
    )
    sku = _optional_text(raw.get("sku"), f"{prefix}.sku", max_length=255)
    classification_status = _text(
        raw.get("classification_status"),
        f"{prefix}.classification_status",
        max_length=32,
    )
    return RecoveredEvent(
        idempotency_key=idempotency_key,
        sequence=sequence,
        captured_at=captured_at,
        local_day=local_day,
        camera=camera,
        source=source,
        model_event_origin=origin,
        source_row_id=source_row_id,
        shadow_run_id=shadow_run_id,
        class_name=class_name,
        color=color,
        normalized_color=_normalized_color(color, f"{prefix}.color"),
        color_confidence=color_confidence,
        brand=brand,
        normalized_brand=_normalized_brand(brand),
        brand_confidence=brand_confidence,
        sku=sku,
        classification_status=classification_status,
    )


def _aggregate(events: tuple[RecoveredEvent, ...]) -> dict[str, dict[str, Any]]:
    totals: Counter[date] = Counter()
    colors: defaultdict[date, Counter[str]] = defaultdict(Counter)
    brands: defaultdict[date, Counter[str]] = defaultdict(Counter)
    for event in events:
        totals[event.local_day] += 1
        colors[event.local_day][event.normalized_color] += 1
        brands[event.local_day][event.normalized_brand] += 1
    return {
        day.isoformat(): {
            "total": totals[day],
            "per_color": dict(sorted(colors[day].items())),
            "per_brand": dict(sorted(brands[day].items())),
        }
        for day in sorted(totals)
    }


def _validate_declared_summaries(
    payload: dict[str, Any], events: tuple[RecoveredEvent, ...]
) -> None:
    count = len(events)
    dedup = _object(payload.get("deduplication"), "deduplication")
    exported = _integer(dedup.get("exported_events"), "deduplication.exported_events")
    raw_count = _integer(dedup.get("raw_events"), "deduplication.raw_events")
    suppressed = _integer(
        dedup.get("duplicates_suppressed"), "deduplication.duplicates_suppressed"
    )
    _number(dedup.get("window_seconds"), "deduplication.window_seconds", minimum=0)
    _number(dedup.get("distance_pixels"), "deduplication.distance_pixels", minimum=0)
    if exported != count or raw_count != exported + suppressed:
        raise _fail("deduplication counts do not match events")

    summary = _object(payload.get("summary"), "summary")
    if _integer(summary.get("total"), "summary.total") != count:
        raise _fail("summary.total does not match events")
    declared_origins = _object(summary.get("by_origin"), "summary.by_origin")
    actual_origins = dict(sorted(Counter(e.model_event_origin for e in events).items()))
    if declared_origins != actual_origins:
        raise _fail("summary.by_origin does not match events")

    declared_days = _object(summary.get("by_local_date"), "summary.by_local_date")
    actual_days: dict[str, dict[str, Any]] = {}
    for event in events:
        item = actual_days.setdefault(
            event.local_day.isoformat(), {"total": 0, "per_class": {}}
        )
        item["total"] += 1
        item["per_class"][event.class_name] = (
            item["per_class"].get(event.class_name, 0) + 1
        )
    actual_days = {
        day: {
            "total": item["total"],
            "per_class": dict(sorted(item["per_class"].items())),
        }
        for day, item in sorted(actual_days.items())
    }
    normalized_declared: dict[str, dict[str, Any]] = {}
    for day, raw_item in declared_days.items():
        item = _object(raw_item, f"summary.by_local_date.{day}")
        normalized_declared[day] = {
            "total": item.get("total"),
            "per_class": dict(sorted(_object(item.get("per_class"), "per_class").items())),
        }
    if normalized_declared != actual_days:
        raise _fail("summary.by_local_date does not match events")


def load_manual_analytics_document(
    path_value: str | Path, *, expected_sha256: str
) -> ManualAnalyticsDocument:
    """Read and fully validate a recovery file before any database access."""

    path = Path(path_value)
    expected_sha256 = str(expected_sha256).strip().lower()
    if SHA256_RE.fullmatch(expected_sha256) is None:
        raise _fail("--expected-sha256 must contain 64 lowercase hex characters")
    try:
        size = path.stat().st_size
        if size <= 0 or size > MAX_FILE_BYTES:
            raise _fail(f"file size must be between 1 and {MAX_FILE_BYTES} bytes")
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise _fail(f"cannot read import file: {exc}") from exc
    actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha256 != expected_sha256:
        raise _fail(
            f"file SHA256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    try:
        payload = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(f"file is not valid UTF-8 JSON: {exc}") from exc
    payload = _object(payload, "document")
    if payload.get("schema") != SCHEMA_NAME:
        raise _fail(f"schema must be {SCHEMA_NAME}")
    _aware_datetime(payload.get("generated_at"), "generated_at")

    model = _object(payload.get("model"), "model")
    if model.get("id") != MODEL_ID:
        raise _fail(f"model.id must be {MODEL_ID}")
    model_sha256 = _text(model.get("sha256"), "model.sha256", max_length=64)
    if SHA256_RE.fullmatch(model_sha256) is None:
        raise _fail("model.sha256 must contain 64 lowercase hex characters")

    coverage = _object(payload.get("coverage"), "coverage")
    camera = _text(coverage.get("camera"), "coverage.camera", max_length=32)
    if CAMERA_RE.fullmatch(camera) is None:
        raise _fail("coverage.camera must match cam<N>")
    source = _text(
        coverage.get("source"),
        "coverage.source",
        max_length=16,
        allowed={"main", "sub"},
    )
    if coverage.get("analytics_scope") != ANALYTICS_SCOPE:
        raise _fail(f"coverage.analytics_scope must be {ANALYTICS_SCOPE}")
    threshold = _confidence(
        coverage.get("count_confidence_threshold"),
        "coverage.count_confidence_threshold",
    )
    assert threshold is not None
    coverage_from = _aware_datetime(coverage.get("from_local"), "coverage.from_local")
    coverage_to = _aware_datetime(coverage.get("to_utc"), "coverage.to_utc")
    shadow_until = _aware_datetime(
        coverage.get("shadow_candidate_until_utc"),
        "coverage.shadow_candidate_until_utc",
    )
    production_from = _aware_datetime(
        coverage.get("production_from_utc"),
        "coverage.production_from_utc",
    )
    if coverage_from >= coverage_to:
        raise _fail("coverage.from_local must be before coverage.to_utc")
    if not coverage_from <= shadow_until < production_from <= coverage_to:
        raise _fail("coverage shadow/production transition is inconsistent")

    raw_events = payload.get("events")
    if not isinstance(raw_events, list) or not 1 <= len(raw_events) <= MAX_EVENTS:
        raise _fail(f"events must contain between 1 and {MAX_EVENTS} items")
    events = tuple(
        _validate_event(
            raw_event,
            index=index,
            camera=camera,
            source=source,
            model_sha256=model_sha256,
            threshold=threshold,
        )
        for index, raw_event in enumerate(raw_events, start=1)
    )
    if len({event.idempotency_key for event in events}) != len(events):
        raise _fail("events contain duplicate idempotency_key values")
    if any(
        current.captured_at < previous.captured_at
        for previous, current in pairwise(events)
    ):
        raise _fail("events must be ordered by captured_at")
    if events[0].captured_at < coverage_from or events[-1].captured_at != coverage_to:
        raise _fail("event timestamps do not match declared coverage")
    shadow_events = tuple(
        event for event in events if event.model_event_origin == "shadow_candidate"
    )
    production_events = tuple(
        event for event in events if event.model_event_origin == "production"
    )
    if not shadow_events or not production_events:
        raise _fail("events must contain both shadow_candidate and production rows")
    if (
        shadow_events[-1].captured_at != shadow_until
        or production_events[0].captured_at != production_from
        or any(event.captured_at > shadow_until for event in shadow_events)
        or any(event.captured_at < production_from for event in production_events)
    ):
        raise _fail("events overlap or disagree with the declared model transition")

    _validate_declared_summaries(payload, events)
    return ManualAnalyticsDocument(
        path=path,
        file_sha256=actual_sha256,
        schema_name=SCHEMA_NAME,
        model_id=MODEL_ID,
        model_sha256=model_sha256,
        camera=camera,
        source=source,
        analytics_scope=ANALYTICS_SCOPE,
        events=events,
        per_day=_aggregate(events),
    )


def _batch_metadata(document: ManualAnalyticsDocument) -> dict[str, Any]:
    return {
        "schema_name": document.schema_name,
        "model_id": document.model_id,
        "model_sha256": document.model_sha256,
        "camera": document.camera,
        "source": document.source,
        "analytics_scope": document.analytics_scope,
        "event_count": len(document.events),
        "first_captured_at": document.first_captured_at,
        "last_captured_at": document.last_captured_at,
        "per_day": document.per_day,
    }


def _event_metadata(event: RecoveredEvent) -> dict[str, Any]:
    return {
        "idempotency_key": event.idempotency_key,
        "sequence": event.sequence,
        "captured_at": event.captured_at,
        "local_day": event.local_day,
        "camera": event.camera,
        "source": event.source,
        "model_event_origin": event.model_event_origin,
        "source_row_id": event.source_row_id,
        "shadow_run_id": event.shadow_run_id,
        "class_name": event.class_name,
        "color": event.color,
        "color_confidence": event.color_confidence,
        "brand": event.brand,
        "brand_confidence": event.brand_confidence,
        "sku": event.sku,
        "classification_status": event.classification_status,
    }


def _assert_completed_batch(
    batch: ManualBagAnalyticsImportBatch, document: ManualAnalyticsDocument
) -> None:
    for field, expected in _batch_metadata(document).items():
        if getattr(batch, field) != expected:
            raise _fail(f"existing import batch has changed field {field}")
    rows = list(batch.events.order_by("sequence"))
    if len(rows) != len(document.events):
        raise _fail("existing import batch has an incomplete event ledger")
    for row, event in zip(rows, document.events):
        for field, expected in _event_metadata(event).items():
            if getattr(row, field) != expected:
                raise _fail(
                    "existing import event ledger has changed contents at "
                    f"sequence {event.sequence}"
                )


def _same_optional_float(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return math.isclose(float(left), float(right), rel_tol=0, abs_tol=0.000001)


def _assert_production_anchors_consumed(
    document: ManualAnalyticsDocument, *, lock: bool
) -> None:
    """Prove the native production tail cannot later double-count the file.

    Manual shadow rows deliberately live outside ``AlwaysOnImportedEvent``.
    Production rows are different: their ``source_row_id`` is a real camera-PC
    journal id.  Every one must already be durably traversed and explicitly
    marked as not applied before an analytics-only replacement is accepted.
    """

    production_events = tuple(
        event
        for event in document.events
        if event.model_event_origin == "production"
    )
    if not production_events:
        raise _fail("file has no production events to anchor cursor continuity")

    cursors = AlwaysOnCounterCursor.objects
    if lock:
        cursors = cursors.select_for_update()
    cursor = cursors.filter(camera=document.camera).first()
    required_cursor = max(event.source_row_id for event in production_events)
    if cursor is None or cursor.last_event_id is None:
        raise _fail("camera event cursor repair has not been completed")
    if cursor.last_event_id < required_cursor:
        raise _fail(
            "camera event cursor has not consumed the production anchor range"
        )

    rows = AlwaysOnImportedEvent.objects
    if lock:
        rows = rows.select_for_update()
    by_upstream_id = {
        row.upstream_event_id: row
        for row in rows.filter(
            camera=document.camera,
            upstream_event_id__in=[
                event.source_row_id for event in production_events
            ],
        )
    }
    if len(by_upstream_id) != len(production_events):
        raise _fail("production anchor event ledger is incomplete")

    for event in production_events:
        row = by_upstream_id[event.source_row_id]
        if row.applied_to_analytics:
            raise _fail(
                f"production anchor {event.source_row_id} was already applied"
            )
        if row.total_after is None:
            raise _fail(
                f"production anchor {event.source_row_id} has no upstream total"
            )
        if (
            row.occurred_at != event.captured_at
            or row.source != event.source
            or row.mode != "always_on"
            or row.class_name != event.class_name
            or row.color != event.color
            or not _same_optional_float(
                row.color_confidence, event.color_confidence
            )
            or row.brand != event.brand
            or not _same_optional_float(
                row.brand_confidence, event.brand_confidence
            )
            or row.sku != event.sku
            or row.classification_status != event.classification_status
        ):
            raise _fail(
                f"production anchor {event.source_row_id} changed contents"
            )


def _inspect_state(
    document: ManualAnalyticsDocument, *, lock: bool
) -> tuple[str, ManualBagAnalyticsImportBatch | None]:
    # The camera cursor is the per-camera import mutex. Acquire it before
    # checking a possibly absent batch so concurrent first-time imports cannot
    # both observe an empty ledger and race into the additive daily update.
    if lock:
        _assert_production_anchors_consumed(document, lock=True)

    batches = ManualBagAnalyticsImportBatch.objects
    if lock:
        batches = batches.select_for_update()
    batch = batches.filter(file_sha256=document.file_sha256).first()
    if batch is not None:
        _assert_completed_batch(batch, document)
        return "already_imported", batch

    event_rows = ManualBagAnalyticsImportEvent.objects
    if lock:
        event_rows = event_rows.select_for_update()
    if event_rows.filter(
        idempotency_key__in=[event.idempotency_key for event in document.events]
    ).exists():
        raise _fail("event ledger is in a mixed state without the matching batch")

    if not lock:
        _assert_production_anchors_consumed(document, lock=False)

    days = [date.fromisoformat(value) for value in document.per_day]
    daily_rows = AlwaysOnDailyAnalytics.objects
    if lock:
        daily_rows = daily_rows.select_for_update()
    for row in daily_rows.filter(camera=document.camera, day__in=days):
        if row.archived_at is not None or row.archive_id is not None:
            raise _fail(
                f"target analytics row {row.camera}/{row.day} is already archived"
            )
        if not isinstance(row.model_per_color, dict) or not isinstance(
            row.model_per_brand, dict
        ):
            raise _fail(f"target analytics row {row.camera}/{row.day} is malformed")
    return "ready", None


def inspect_manual_analytics_import(
    document: ManualAnalyticsDocument,
) -> ManualAnalyticsImportResult:
    """Return a no-write plan after checking for replay or mixed state."""

    status, batch = _inspect_state(document, lock=False)
    return ManualAnalyticsImportResult(
        status="already_imported" if status == "already_imported" else "would_import",
        batch_id=batch.pk if batch is not None else None,
    )


def _merged_breakdown(existing: object, addition: dict[str, int]) -> dict[str, int]:
    if not isinstance(existing, dict):
        raise _fail("target analytics breakdown is malformed")
    merged: dict[str, int] = {}
    for key, value in existing.items():
        if (
            not isinstance(key, str)
            or not key
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise _fail("target analytics breakdown is malformed")
        merged[key] = value
    for key, value in addition.items():
        merged[key] = merged.get(key, 0) + value
    return merged


def apply_manual_analytics_import(
    document: ManualAnalyticsDocument,
) -> ManualAnalyticsImportResult:
    """Atomically add analytics and its complete independent audit ledger."""

    try:
        with transaction.atomic():
            state, existing_batch = _inspect_state(document, lock=True)
            if state == "already_imported":
                assert existing_batch is not None
                return ManualAnalyticsImportResult(
                    status="already_imported", batch_id=existing_batch.pk
                )

            batch = ManualBagAnalyticsImportBatch.objects.create(
                file_sha256=document.file_sha256,
                source_filename=document.path.name,
                **_batch_metadata(document),
            )
            ManualBagAnalyticsImportEvent.objects.bulk_create(
                [
                    ManualBagAnalyticsImportEvent(batch=batch, **_event_metadata(event))
                    for event in document.events
                ],
                batch_size=1000,
            )
            for day_value, addition in document.per_day.items():
                local_day = date.fromisoformat(day_value)
                try:
                    row = AlwaysOnDailyAnalytics.objects.select_for_update().get(
                        camera=document.camera,
                        day=local_day,
                    )
                except AlwaysOnDailyAnalytics.DoesNotExist:
                    AlwaysOnDailyAnalytics.objects.create(
                        camera=document.camera,
                        day=local_day,
                        model_total=addition["total"],
                        model_per_color=addition["per_color"],
                        model_per_brand=addition["per_brand"],
                    )
                    continue
                if row.archived_at is not None or row.archive_id is not None:
                    raise _fail(
                        f"target analytics row {row.camera}/{row.day} is archived"
                    )
                row.model_total += addition["total"]
                row.model_per_color = _merged_breakdown(
                    row.model_per_color, addition["per_color"]
                )
                row.model_per_brand = _merged_breakdown(
                    row.model_per_brand, addition["per_brand"]
                )
                row.save(
                    update_fields=[
                        "model_total",
                        "model_per_color",
                        "model_per_brand",
                        "updated_at",
                    ]
                )
            return ManualAnalyticsImportResult(status="imported", batch_id=batch.pk)
    except IntegrityError as exc:
        raise _fail(
            "database changed concurrently; no part of the import was committed"
        ) from exc
