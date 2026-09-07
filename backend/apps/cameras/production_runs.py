"""Production shift boundaries and the ordered, durable colour-run ledger."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from . import ai
from .models import (
    ANALYTICS_SCOPE_AI247,
    AlwaysOnProductionCorrection,
    AlwaysOnProductionRun,
    ContinuousCameraRole,
)

log = logging.getLogger(__name__)
CLOSE_TIME = time(hour=19)
RUN_GAP = timedelta(minutes=5)


def _default_timezone():
    """Use the configured plant timezone, not a request-specific override."""

    return timezone.get_default_timezone()


def _aware(value: datetime | None = None) -> datetime:
    value = value or timezone.now()
    if timezone.is_naive(value):
        value = timezone.make_aware(value, _default_timezone())
    return value


def _local(value: datetime | None = None) -> datetime:
    return timezone.localtime(_aware(value), _default_timezone())


def _iso(value: datetime | None) -> str | None:
    return _local(value).isoformat() if value is not None else None


def business_day_for(value: datetime | None = None) -> date:
    """Return the production day whose shift closes at 19:00 Almaty time."""

    local = _local(value)
    if local.timetz().replace(tzinfo=None) >= CLOSE_TIME:
        return local.date() + timedelta(days=1)
    return local.date()


def scheduled_for(day: date) -> datetime:
    """Return the aware 19:00 cutoff for a production day."""

    return timezone.make_aware(datetime.combine(day, CLOSE_TIME), _default_timezone())


def _normalize_color(value: object) -> str:
    color = " ".join(str(value or "").split()).lower()
    if not color or len(color) > 32:
        raise ValidationError({"color": "Укажите цвет до 32 символов"})
    return color


def _positive_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        result = int(value)
    except (OverflowError, TypeError, ValueError):
        return 0
    return max(0, result)


@transaction.atomic
def record_color_deltas(
    camera: str,
    color_deltas: dict[str, int] | None,
    observed_at: datetime,
    total_delta: int,
    *,
    ordered_color_event: bool = False,
) -> list[AlwaysOnProductionRun]:
    """Append one counter delta to contiguous per-colour production runs.

    Durable journal events arrive in an authoritative order and contain one
    detected colour.  For those events, a different colour ends every other
    open run before the new run is recorded.  Legacy aggregate snapshots can
    contain deltas for several colours without preserving their order, so they
    deliberately keep the previous per-colour fallback behaviour.
    """

    camera = ai.normalize(camera)
    observed_at = _aware(observed_at)
    business_day = business_day_for(observed_at)
    normalized: dict[str, int] = defaultdict(int)
    for raw_color, raw_bags in (color_deltas or {}).items():
        bags = _positive_int(raw_bags)
        if not bags:
            continue
        try:
            color = _normalize_color(raw_color)
        except ValidationError:
            continue
        normalized[color] += bags

    total = _positive_int(total_delta)
    classified_total = sum(normalized.values())
    if classified_total > total:
        # A malformed/reset worker reply must never add more stock than the
        # authoritative total delta. Keep the largest colour deltas first and
        # surface the mismatch in logs instead of silently over-receiving.
        log.warning(
            "AI 24/7 colour delta exceeds total camera=%s colors=%s total=%s",
            camera,
            classified_total,
            total,
        )
        remaining = total
        bounded: dict[str, int] = {}
        for color, bags in sorted(
            normalized.items(), key=lambda item: (-item[1], item[0])
        ):
            accepted = min(bags, remaining)
            if accepted:
                bounded[color] = accepted
                remaining -= accepted
            if remaining <= 0:
                break
        normalized = defaultdict(int, bounded)
    unclassified = total - sum(normalized.values())
    if unclassified > 0:
        normalized["unclassified"] += unclassified
    if not normalized:
        return []

    open_run_rows = list(
        AlwaysOnProductionRun.objects.select_for_update().filter(
            camera=camera,
            ended_at__isnull=True,
        )
    )
    open_runs = {row.color: row for row in open_run_rows}
    if ordered_color_event:
        if len(normalized) != 1:
            raise ValueError("ordered color event must resolve to exactly one color")
        event_color = next(iter(normalized))

        # A backend deployed over the legacy per-colour implementation may
        # inherit several open rows.  Only the most recently counted row was
        # the real current colour; an older row with the incoming colour must
        # never be revived across an intervening colour.
        current_row = max(
            open_run_rows,
            key=lambda row: (row.last_counted_at, row.pk),
            default=None,
        )
        for row in open_run_rows:
            if row == current_row:
                continue
            row.ended_at = row.last_counted_at
            row.save(update_fields=["ended_at", "updated_at"])
            open_runs.pop(row.color, None)
        if current_row is not None and current_row.color != event_color:
            current_row.ended_at = current_row.last_counted_at
            current_row.save(update_fields=["ended_at", "updated_at"])
            open_runs.pop(current_row.color, None)

    touched: list[AlwaysOnProductionRun] = []
    for color in sorted(normalized):
        bags = normalized[color]
        row = open_runs.get(color)
        if row is not None:
            elapsed = observed_at - row.last_counted_at
            must_reopen = (
                row.business_day != business_day
                # Warehouse shifts span midnight, but analytics bars do not.
                # Split here so each run and its bag count belongs to exactly
                # one local calendar day while both halves retain the same
                # 19:00-based business day for stock posting.
                or _local(row.last_counted_at).date() != _local(observed_at).date()
                or elapsed > RUN_GAP
            )
            if must_reopen:
                row.ended_at = row.last_counted_at
                row.save(update_fields=["ended_at", "updated_at"])
                row = None
            elif elapsed < timedelta(0):
                # A delayed duplicate must not move a run backwards, but its
                # already-authoritative counter delta must still be retained.
                observed_for_row = row.last_counted_at
            else:
                observed_for_row = observed_at
        else:
            observed_for_row = observed_at

        if row is None:
            # The analytics cursor serializes normal calls for one camera.  The
            # partial unique constraint is the final fence if an administrative
            # or test caller invokes this service concurrently.
            try:
                with transaction.atomic():
                    row = AlwaysOnProductionRun.objects.create(
                        camera=camera,
                        business_day=business_day,
                        color=color,
                        started_at=observed_at,
                        last_counted_at=observed_at,
                        model_bags=bags,
                        is_approximate=color == "unclassified",
                    )
            except IntegrityError:
                row = AlwaysOnProductionRun.objects.select_for_update().get(
                    camera=camera,
                    color=color,
                    ended_at__isnull=True,
                )
                # The winner can only represent this same interval.  If it was
                # closed across a boundary, retrying on the next poll is safer
                # than ever merging two production days.
                if row.business_day != business_day:
                    raise
                row.model_bags += bags
                row.last_counted_at = max(row.last_counted_at, observed_at)
                row.is_approximate = row.is_approximate or color == "unclassified"
                row.save(
                    update_fields=[
                        "model_bags",
                        "last_counted_at",
                        "is_approximate",
                        "updated_at",
                    ]
                )
        else:
            row.model_bags += bags
            row.last_counted_at = observed_for_row
            row.is_approximate = row.is_approximate or color == "unclassified"
            row.save(
                update_fields=[
                    "model_bags",
                    "last_counted_at",
                    "is_approximate",
                    "updated_at",
                ]
            )
        touched.append(row)
    return touched


@transaction.atomic
def close_stale_runs(
    now: datetime | None = None,
    *,
    reserved_ai247_only: bool = False,
) -> int:
    """Close runs after five quiet minutes and always at a shift boundary."""

    now = _aware(now)
    current_day = business_day_for(now)
    threshold = now - RUN_GAP
    rows_query = AlwaysOnProductionRun.objects.filter(
        ended_at__isnull=True,
    ).filter(~Q(business_day=current_day) | Q(last_counted_at__lt=threshold))
    if reserved_ai247_only:
        rows_query = rows_query.filter(
            camera__in=ContinuousCameraRole.objects.filter(
                analytics_scope=ANALYTICS_SCOPE_AI247,
            ).values("camera")
        )
    # Never wait behind ingestion or invert its order when a legacy camera
    # has several open colours. Busy rows are retried on the next worker tick.
    row_ids = list(
        rows_query.select_for_update(skip_locked=True).values_list("pk", flat=True)
    )
    return rows_query.filter(pk__in=row_ids).update(
        ended_at=F("last_counted_at"), updated_at=now
    )


def effective_ended_at(row: AlwaysOnProductionRun, now: datetime) -> datetime | None:
    """Show a quiet interval as closed while leaving persistence to the worker."""
    if row.ended_at is not None:
        return row.ended_at
    if row.business_day != business_day_for(now) or row.last_counted_at < now - RUN_GAP:
        return row.last_counted_at
    return None


def _day_totals(camera: str, business_day: date) -> dict[str, dict[str, int]]:
    detected = {
        row["color"]: int(row["bags"] or 0)
        for row in AlwaysOnProductionRun.objects.filter(
            camera=camera,
            business_day=business_day,
        )
        .values("color")
        .annotate(bags=Sum("model_bags"))
    }
    corrections = {
        row["color"]: int(row["bags"] or 0)
        for row in AlwaysOnProductionCorrection.objects.filter(
            camera=camera,
            business_day=business_day,
        )
        .values("color")
        .annotate(bags=Sum("delta"))
    }
    return {
        color: {
            "detected_bags": detected.get(color, 0),
            "correction_bags": corrections.get(color, 0),
            "net_bags": detected.get(color, 0) + corrections.get(color, 0),
        }
        for color in sorted(set(detected) | set(corrections))
    }
