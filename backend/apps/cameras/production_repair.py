"""Guarded reconstruction of exact production colour periods from events.

This module is intentionally separate from live ingestion.  It is an
operator-only repair for a bounded interval where the durable imported event
journal and the already-accounted production totals agree exactly.  The
repair changes period boundaries only; it must never move bags between stock
or analytics buckets.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from . import ai, production
from .models import (
    AlwaysOnCounterCursor,
    AlwaysOnImportedEvent,
    AlwaysOnProductionRun,
)


class ProductionRepairError(Exception):
    """The requested reconstruction cannot be proven balance-neutral."""


@dataclass(frozen=True)
class RebuiltRun:
    business_day: date
    color: str
    started_at: datetime
    last_counted_at: datetime
    ended_at: datetime | None
    model_bags: int
    is_approximate: bool


@dataclass(frozen=True)
class ProductionRepairResult:
    camera: str
    local_day: date
    boundary_at: datetime
    last_event_at: datetime
    event_count: int
    existing_run_count: int
    rebuilt_run_count: int
    per_color: dict[str, int]
    applied: bool
    would_change: bool


def _day_window(local_day: date) -> tuple[datetime, datetime]:
    plant_timezone = timezone.get_default_timezone()
    start = timezone.make_aware(
        datetime.combine(local_day, time.min),
        plant_timezone,
    )
    end = timezone.make_aware(
        datetime.combine(local_day + timedelta(days=1), time.min),
        plant_timezone,
    )
    return start, end


def _local_day(value: datetime) -> date:
    return timezone.localtime(value, timezone.get_default_timezone()).date()


def _plant_today() -> date:
    return timezone.localtime(
        timezone.now(), timezone.get_default_timezone()
    ).date()


def _event_color(event: AlwaysOnImportedEvent) -> str:
    # Keep this byte-for-byte compatible with event_sync._event_color followed
    # by production.record_color_deltas(total_delta=1): an absent/invalid
    # class is retained as one approximate, unclassified bag.
    color = (event.color or event.class_name).split("_", 1)[0].strip().lower()
    return color if color and len(color) <= 32 else "unclassified"


def _segment_events(
    events: list[AlwaysOnImportedEvent],
) -> list[RebuiltRun]:
    rebuilt: list[RebuiltRun] = []
    for event in events:
        color = _event_color(event)
        event_business_day = production.business_day_for(event.occurred_at)
        can_continue = False
        if rebuilt:
            current = rebuilt[-1]
            gap = event.occurred_at - current.last_counted_at
            can_continue = (
                color == current.color
                and timedelta(0) <= gap <= production.RUN_GAP
                and _local_day(event.occurred_at) == _local_day(current.started_at)
                and event_business_day == current.business_day
            )

        if can_continue:
            current = rebuilt[-1]
            rebuilt[-1] = replace(
                current,
                last_counted_at=event.occurred_at,
                ended_at=event.occurred_at,
                model_bags=current.model_bags + 1,
            )
            continue

        rebuilt.append(
            RebuiltRun(
                business_day=event_business_day,
                color=color,
                started_at=event.occurred_at,
                last_counted_at=event.occurred_at,
                ended_at=event.occurred_at,
                model_bags=1,
                is_approximate=color == "unclassified",
            )
        )
    return rebuilt


def _color_totals(rows) -> Counter[str]:
    totals: Counter[str] = Counter()
    for row in rows:
        totals[row.color] += row.model_bags
    return totals


def _business_color_totals(rows) -> Counter[tuple[date, str]]:
    totals: Counter[tuple[date, str]] = Counter()
    for row in rows:
        totals[(row.business_day, row.color)] += row.model_bags
    return totals


def _calendar_color_totals(rows) -> Counter[tuple[date, str]]:
    totals: Counter[tuple[date, str]] = Counter()
    for row in rows:
        totals[(_local_day(row.started_at), row.color)] += row.model_bags
    return totals


def _run_signature(row) -> tuple:
    return (
        row.business_day,
        row.color,
        row.started_at,
        row.last_counted_at,
        row.ended_at,
        row.model_bags,
        row.is_approximate,
    )


def _validate_candidate_shape(
    *,
    all_runs: list[AlwaysOnProductionRun],
    candidates: list[AlwaysOnProductionRun],
    boundary_at: datetime,
    last_event_at: datetime,
) -> None:
    for row in all_runs:
        if row.started_at >= boundary_at:
            continue
        if row.ended_at is None or row.last_counted_at >= boundary_at:
            raise ProductionRepairError(
                "a pre-boundary production run overlaps the event repair boundary"
            )

    if not candidates or candidates[0].started_at != boundary_at:
        raise ProductionRepairError(
            "candidate production runs do not start at the first applied event"
        )

    for row in candidates:
        if row.started_at > row.last_counted_at:
            raise ProductionRepairError("candidate production run has invalid times")
        if row.last_counted_at > last_event_at or (
            row.ended_at is not None and row.ended_at > last_event_at
        ):
            raise ProductionRepairError(
                "a candidate production run extends beyond exact event coverage"
            )
        if _local_day(row.started_at) != _local_day(row.last_counted_at):
            raise ProductionRepairError(
                "a candidate production run crosses a local calendar boundary"
            )
        if (
            production.business_day_for(row.started_at) != row.business_day
            or production.business_day_for(row.last_counted_at) != row.business_day
        ):
            raise ProductionRepairError(
                "a candidate production run crosses a stock business-day boundary"
            )


def rebuild_event_production_runs(
    *,
    camera: str,
    local_day: date,
    apply: bool = False,
) -> ProductionRepairResult:
    """Plan or apply a balance-neutral reconstruction for one camera/day.

    Dry-run is the default.  On apply the camera event cursor and all of its
    production runs are locked before the same guards are re-evaluated.  Live
    event ingestion takes the cursor lock first as well, so it cannot advance
    the covered journal interval during replacement.
    """

    try:
        camera = ai.normalize(camera)
    except ai.AiError as exc:
        raise ProductionRepairError("invalid camera") from exc
    if not isinstance(local_day, date) or isinstance(local_day, datetime):
        raise ProductionRepairError("local_day must be a date")
    if local_day >= _plant_today():
        raise ProductionRepairError(
            "local_day must be strictly before the current plant-local day"
        )

    day_start, day_end = _day_window(local_day)
    with transaction.atomic():
        cursor_query = AlwaysOnCounterCursor.objects.filter(camera=camera)
        runs_query = AlwaysOnProductionRun.objects.filter(camera=camera).order_by(
            "started_at", "id"
        )
        events_query = AlwaysOnImportedEvent.objects.filter(
            camera=camera,
            occurred_at__gte=day_start,
            occurred_at__lt=day_end,
        ).order_by("occurred_at", "upstream_event_id")
        if apply:
            cursor_query = cursor_query.select_for_update()
            runs_query = runs_query.select_for_update()
            events_query = events_query.select_for_update()

        cursor = cursor_query.first()
        if cursor is None or cursor.last_event_id is None:
            raise ProductionRepairError(
                "camera has no durable event cursor to lock and verify"
            )
        all_runs = list(runs_query)
        all_day_events = list(events_query)
        applied_events = [
            event
            for event in all_day_events
            if event.applied_to_analytics
        ]
        if not applied_events:
            raise ProductionRepairError(
                "no applied continuous-analytics events exist for camera and local day"
            )
        if any(
            event.applied_to_analytics
            and not (
                event.mode == "always_on"
                or (event.mode == "session" and event.continuous_analytics)
            )
            for event in all_day_events
        ):
            raise ProductionRepairError("imported event application state is invalid")

        boundary_at = applied_events[0].occurred_at
        last_event_at = applied_events[-1].occurred_at
        if any(
            (
                event.mode == "always_on"
                or (event.mode == "session" and event.continuous_analytics)
            )
            and not event.applied_to_analytics
            for event in all_day_events
        ):
            raise ProductionRepairError(
                "an unapplied continuous-analytics event exists on the selected local day"
            )
        if cursor.last_event_id < max(
            event.upstream_event_id for event in applied_events
        ):
            raise ProductionRepairError(
                "camera cursor does not cover all selected imported events"
            )
        if any(
            _local_day(row.started_at) == local_day
            and row.started_at > last_event_at
            for row in all_runs
        ):
            raise ProductionRepairError(
                "a production run exists after exact event coverage on the selected day"
            )

        candidates = [
            row
            for row in all_runs
            if boundary_at <= row.started_at <= last_event_at
        ]
        _validate_candidate_shape(
            all_runs=all_runs,
            candidates=candidates,
            boundary_at=boundary_at,
            last_event_at=last_event_at,
        )

        desired = _segment_events(applied_events)

        existing_total = sum(row.model_bags for row in candidates)
        desired_total = sum(row.model_bags for row in desired)
        if existing_total != desired_total:
            raise ProductionRepairError(
                "candidate total does not match imported event total"
            )
        if _color_totals(candidates) != _color_totals(desired):
            raise ProductionRepairError(
                "candidate per-color totals do not match imported events"
            )
        if _business_color_totals(candidates) != _business_color_totals(desired):
            raise ProductionRepairError(
                "candidate stock business-day/color totals do not match events"
            )
        if _calendar_color_totals(candidates) != _calendar_color_totals(desired):
            raise ProductionRepairError(
                "candidate calendar-day/color totals do not match events"
            )

        existing_signature = [_run_signature(row) for row in candidates]
        desired_signature = [_run_signature(row) for row in desired]
        would_change = existing_signature != desired_signature
        if apply and would_change:
            candidate_ids = [row.pk for row in candidates]
            AlwaysOnProductionRun.objects.filter(pk__in=candidate_ids).delete()
            AlwaysOnProductionRun.objects.bulk_create(
                [
                    AlwaysOnProductionRun(
                        camera=camera,
                        business_day=row.business_day,
                        color=row.color,
                        started_at=row.started_at,
                        last_counted_at=row.last_counted_at,
                        ended_at=row.ended_at,
                        model_bags=row.model_bags,
                        is_approximate=row.is_approximate,
                    )
                    for row in desired
                ]
            )

        return ProductionRepairResult(
            camera=camera,
            local_day=local_day,
            boundary_at=boundary_at,
            last_event_at=last_event_at,
            event_count=len(applied_events),
            existing_run_count=len(candidates),
            rebuilt_run_count=len(desired),
            per_color=dict(sorted(_color_totals(desired).items())),
            applied=apply,
            would_change=would_change,
        )
