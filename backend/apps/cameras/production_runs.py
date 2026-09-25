"""Production shift boundaries and the ordered, durable colour-run ledger."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from . import ai
from .models import (
    ANALYTICS_SCOPE_AI247,
    AlwaysOnProductionCorrection,
    AlwaysOnProductionRun,
    AlwaysOnStockBatch,
    ContinuousCameraRole,
)

CLOSE_TIME = time(hour=19)
RUN_GAP = timedelta(minutes=5)
# A posted/empty shift is immutable: stock was received (or nothing was due).
TERMINAL_BATCH_STATUSES = frozenset(
    {AlwaysOnStockBatch.POSTED, AlwaysOnStockBatch.EMPTY}
)


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


def local_date(value: datetime | None = None) -> date:
    """Calendar date of ``value`` (default: now) in the plant timezone."""

    return _local(value).date()


def local_day_window(first: date, last: date | None = None) -> tuple[datetime, datetime]:
    """[first 00:00, day after ``last`` 00:00) in the plant timezone."""

    zone = _default_timezone()
    return (
        timezone.make_aware(datetime.combine(first, time.min), zone),
        timezone.make_aware(
            datetime.combine((last or first) + timedelta(days=1), time.min), zone
        ),
    )


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


@transaction.atomic
def record_color_event(
    camera: str,
    color: str | None,
    observed_at: datetime,
) -> AlwaysOnProductionRun:
    """Append one counted bag to the camera's contiguous colour run.

    Journal events arrive in authoritative order with one colour each, so a
    camera has at most one open run: a different colour ends it before the
    new run starts.  A bag without a usable colour counts as "unclassified".
    """

    camera = ai.normalize(camera)
    observed_at = _aware(observed_at)
    business_day = business_day_for(observed_at)
    try:
        color = _normalize_color(color)
    except ValidationError:
        color = "unclassified"

    open_runs = list(
        AlwaysOnProductionRun.objects.select_for_update().filter(
            camera=camera,
            ended_at__isnull=True,
        )
    )
    # Only the most recently counted run is the current colour; any other
    # open row must never be revived across an intervening colour.
    current = max(
        open_runs, key=lambda row: (row.last_counted_at, row.pk), default=None
    )
    for row in open_runs:
        if row is not current or row.color != color:
            row.ended_at = row.last_counted_at
            row.save(update_fields=["ended_at", "updated_at"])
    row = current if current is not None and current.color == color else None

    observed_for_row = observed_at
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
            # already-authoritative count must still be retained.
            observed_for_row = row.last_counted_at

    if row is None:
        # The caller owns the camera's AlwaysOnCounterCursor lock, which
        # serializes these calls; the partial unique constraint on open
        # runs is only the last fence and fails the whole page.
        return AlwaysOnProductionRun.objects.create(
            camera=camera,
            business_day=business_day,
            color=color,
            started_at=observed_at,
            last_counted_at=observed_at,
            model_bags=1,
            is_approximate=color == "unclassified",
        )
    row.model_bags += 1
    row.last_counted_at = observed_for_row
    row.is_approximate = row.is_approximate or color == "unclassified"
    row.save(
        update_fields=["model_bags", "last_counted_at", "is_approximate", "updated_at"]
    )
    return row


@transaction.atomic
def close_stale_runs(now: datetime | None = None) -> int:
    """Close AI 24/7 runs after five quiet minutes and at a shift boundary."""

    now = _aware(now)
    current_day = business_day_for(now)
    threshold = now - RUN_GAP
    rows_query = AlwaysOnProductionRun.objects.filter(
        ended_at__isnull=True,
        camera__in=ContinuousCameraRole.objects.filter(
            analytics_scope=ANALYTICS_SCOPE_AI247,
        ).values("camera"),
    ).filter(~Q(business_day=current_day) | Q(last_counted_at__lt=threshold))
    # Never wait behind ingestion or invert its order. Busy rows are retried
    # on the next worker tick.
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


def _day_totals(camera: str, business_day: date) -> dict[str, dict]:
    """Per-colour production of one shift: the single posting/preview source.

    ``detected`` is the camera's run ledger, ``resolved`` moves bags the camera
    left as ``unknown`` to the colour CRM resolved for them (neighbours, votes
    or an operator), ``correction`` sums the historical audited manual
    subtractions of the shift.
    """

    # color_resolution imports this module for shift boundaries.
    from .color_resolution import business_day_transfers

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
    transfers = business_day_transfers(
        camera,
        business_day,
        available={
            color: detected.get(color, 0) + corrections.get(color, 0)
            for color in detected
        },
    )
    result: dict[str, dict] = {}
    for color in sorted(set(detected) | set(corrections) | set(transfers.delta)):
        counts = {
            "detected_bags": detected.get(color, 0),
            "resolved_bags": transfers.delta.get(color, 0),
            "correction_bags": corrections.get(color, 0),
        }
        result[color] = {
            **counts,
            "net_bags": sum(counts.values()),
            "inferred": transfers.inferred.get(color, {}),
        }
    return result
