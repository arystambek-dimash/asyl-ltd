"""Durable AI 24/7 production periods and automatic warehouse receipts.

The daily chart is intentionally not used as a warehouse source.  It can be
archived or adjusted for presentation, while the rows in this module form an
append-only production ledger with an idempotent posting batch per camera and
production day.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.catalog.models import Product
from apps.eventlog.services import log_event
from apps.warehouse.services import receive_stock

from . import ai
from .models import (
    AlwaysOnColorProductMapping,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    AlwaysOnProductionCorrection,
    AlwaysOnProductionRun,
    AlwaysOnStockBatch,
    AlwaysOnStockPosting,
)

log = logging.getLogger(__name__)

CLOSE_TIME = time(hour=19)
RUN_GAP = timedelta(minutes=5)
# Let the 30-second event monitor observe a clean page after the shift cutoff
# before warehouse stock becomes immutable.  A one-minute delay covers two
# normal monitor starts without making operators wait materially longer.
EVENT_SETTLE_DELAY = timedelta(minutes=1)
BASE_COLORS = ("red", "green", "blue")
RUN_SMOOTHING_N_MIN = 10
COLOR_LABELS = {
    "red": "Красный",
    "green": "Зелёный",
    "blue": "Синий",
    "unclassified": "Без цвета",
}
TERMINAL_BATCH_STATUSES = frozenset(
    {AlwaysOnStockBatch.POSTED, AlwaysOnStockBatch.EMPTY}
)
NON_DOMINANT_BRANDS = frozenset({"unclassified", "unknown"})


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
def close_stale_runs(now: datetime | None = None) -> int:
    """Close runs after five quiet minutes and always at a shift boundary."""

    now = _aware(now)
    current_day = business_day_for(now)
    threshold = now - RUN_GAP
    rows = list(
        AlwaysOnProductionRun.objects.select_for_update().filter(
            ended_at__isnull=True,
        )
    )
    closed = 0
    for row in rows:
        if row.business_day != current_day or row.last_counted_at < threshold:
            row.ended_at = row.last_counted_at
            row.save(update_fields=["ended_at", "updated_at"])
            closed += 1
    return closed


def _product_payload(product: Product) -> dict:
    return {
        "id": product.pk,
        "label": str(product),
        "color": product.color,
        "color_label": dict(Product.COLORS).get(product.color, product.color),
        "weight_kg": str(product.weight_kg),
    }


def _mapping_payload(mapping: AlwaysOnColorProductMapping | None, color: str) -> dict:
    product = mapping.product if mapping is not None else None
    return {
        "color": color,
        "product": product.pk if product is not None else None,
        "product_label": str(product) if product is not None else None,
    }


def _run_payload(
    row: AlwaysOnProductionRun,
    *,
    selected_start: datetime | None = None,
    selected_end: datetime | None = None,
) -> dict:
    result = {
        "id": row.pk,
        "camera": row.camera,
        "business_day": row.business_day.isoformat(),
        "color": row.color,
        "started_at": _iso(row.started_at),
        "last_counted_at": _iso(row.last_counted_at),
        "ended_at": _iso(row.ended_at),
        "model_bags": row.model_bags,
        "is_approximate": row.is_approximate,
        "status": "active" if row.ended_at is None else "closed",
    }
    if selected_start is not None and selected_end is not None:
        starts_before_day = row.started_at < selected_start
        ends_after_day = row.last_counted_at >= selected_end
        result.update(
            {
                "starts_before_day": starts_before_day,
                "ends_after_day": ends_after_day,
                "is_partial_for_day": starts_before_day or ends_after_day,
            }
        )
    return result


def _is_run_smoothing_barrier(run: dict) -> bool:
    """Return whether a display run must remain an exact, isolated anchor.

    A partial legacy run carries the bag count for the whole cross-midnight
    interval rather than only the selected calendar day.  Approximate rows do
    not preserve an authoritative colour sequence either.  Neither is safe to
    merge or use as a sandwich neighbour.
    """

    return bool(run.get("is_partial_for_day") or run.get("is_approximate"))


def _merge_algorithm_runs(left: dict, right: dict) -> dict:
    """Combine adjacent display runs without mutating either source payload."""

    merged = dict(left)
    merged["model_bags"] = int(left["model_bags"]) + int(right["model_bags"])
    merged["last_counted_at"] = right["last_counted_at"]
    merged["ended_at"] = right.get("ended_at")
    merged["status"] = (
        "active"
        if left.get("status") == "active" or right.get("status") == "active"
        else "closed"
    )
    if "ends_after_day" in left or "ends_after_day" in right:
        merged["ends_after_day"] = bool(
            left.get("ends_after_day") or right.get("ends_after_day")
        )
    return merged


def _coalesce_algorithm_runs(runs: list[dict]) -> list[dict]:
    """Coalesce adjacent equal colours, stopping at unreliable run barriers."""

    result: list[dict] = []
    for source in runs:
        run = dict(source)
        if (
            result
            and result[-1].get("color") == run.get("color")
            and not _is_run_smoothing_barrier(result[-1])
            and not _is_run_smoothing_barrier(run)
        ):
            result[-1] = _merge_algorithm_runs(result[-1], run)
        else:
            result.append(run)
    return result


def smooth_day_runs(
    raw_runs: list[dict],
    *,
    n_min: int = RUN_SMOOTHING_N_MIN,
) -> list[dict]:
    """Return the supplied sandwich smoother as a read-only display view.

    Normal runs follow the operator-provided algorithm exactly: adjacent equal
    colours are first coalesced; then the smallest unlocked run below ``n_min``
    is recoloured only when both neighbours have the same other colour.  A run
    at an edge or party boundary is locked and retained.

    The durable production rows remain the raw audit/warehouse source.  This
    helper works on copies and additionally treats partial/approximate legacy
    rows as hard barriers because their sequence or selected-day bag count is
    not exact enough for smoothing.
    """

    runs = _coalesce_algorithm_runs(raw_runs)
    locked: set[tuple[int, object, int]] = set()
    while True:
        candidates = [
            index
            for index, run in enumerate(runs)
            if not _is_run_smoothing_barrier(run)
            and int(run["model_bags"]) < n_min
            and (index, run.get("color"), int(run["model_bags"])) not in locked
        ]
        if not candidates:
            break

        index = min(
            candidates, key=lambda candidate: int(runs[candidate]["model_bags"])
        )
        run = runs[index]
        color = run.get("color")
        bags = int(run["model_bags"])
        previous = runs[index - 1] if index > 0 else None
        following = runs[index + 1] if index < len(runs) - 1 else None
        if (
            previous is not None
            and following is not None
            and not _is_run_smoothing_barrier(previous)
            and not _is_run_smoothing_barrier(following)
            and previous.get("color") == following.get("color")
            and previous.get("color") != color
        ):
            recolored = dict(run)
            recolored["color"] = previous["color"]
            runs[index] = recolored
            runs = _coalesce_algorithm_runs(runs)
        else:
            locked.add((index, color, bags))
    return runs


def _run_color_totals(runs: list[dict]) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for run in runs:
        totals[str(run["color"])] += int(run["model_bags"])
    return dict(sorted(totals.items()))


def _run_smoothing_payload(raw_runs: list[dict]) -> tuple[list[dict], dict]:
    algorithm_runs = smooth_day_runs(raw_runs)
    raw_per_color = _run_color_totals(raw_runs)
    algorithm_per_color = _run_color_totals(algorithm_runs)

    # Reuse the analytics endpoint's largest-remainder rounding so toggling the
    # selected-day cards cannot produce a different percentage convention.
    from .analytics import _color_payload

    metadata = {
        "n_min": RUN_SMOOTHING_N_MIN,
        "changed": algorithm_runs != raw_runs,
        "raw_run_count": len(raw_runs),
        "algorithm_run_count": len(algorithm_runs),
        "raw_model_total": sum(raw_per_color.values()),
        "algorithm_model_total": sum(algorithm_per_color.values()),
        "raw_model_per_color": raw_per_color,
        "algorithm_model_per_color": algorithm_per_color,
        "raw_colors": _color_payload(raw_per_color),
        "algorithm_colors": _color_payload(algorithm_per_color),
    }
    return algorithm_runs, metadata


def _posting_payload(row: AlwaysOnStockPosting) -> dict:
    return {
        "id": row.pk,
        "color": row.color,
        "product": row.product_id,
        "product_label": str(row.product),
        "detected_bags": row.detected_bags,
        "correction_bags": row.correction_bags,
        "posted_bags": row.posted_bags,
        "receipt_id": row.receipt_id,
    }


def _batch_payload(row: AlwaysOnStockBatch) -> dict:
    items = list(row.items.select_related("product", "receipt").all())
    return {
        "id": row.pk,
        "camera": row.camera,
        "business_day": row.business_day.isoformat(),
        "scheduled_for": _iso(row.scheduled_for),
        "status": row.status,
        "total_bags": row.total_bags,
        "last_error": row.last_error,
        "attempts": row.attempts,
        "posted_at": _iso(row.posted_at),
        "items": [_posting_payload(item) for item in items],
    }


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


def _selected_day(value: date | str | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        raise ValidationError({"day": "Укажите дату в формате YYYY-MM-DD"})
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValidationError({"day": "Укажите дату в формате YYYY-MM-DD"})
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(
            {
                "day": "Укажите дату в формате YYYY-MM-DD",
            }
        ) from exc


def _dominant_brand_by_color(
    camera: str,
    selected_day: date | None,
    selected_start: datetime | None,
    selected_end: datetime | None,
) -> dict[str, str | None]:
    """Return the classified brand seen most often for each event colour.

    This is deliberately derived from the durable event journal rather than
    the independent daily brand totals.  It therefore preserves the exact
    colour-to-brand relationship for the selected local calendar day.  Only
    the newest events represented by the active colour totals participate:
    today's events already transferred to an archive must not determine the
    brand of the new active slice.  If the journal does not fully cover an
    active colour (for example, because its baseline predates event sync), the
    brand remains unknown.  A lexical tie-break keeps the result stable
    regardless of database order.
    """

    if selected_day is None or selected_start is None or selected_end is None:
        return {}

    model_per_color = (
        AlwaysOnDailyAnalytics.objects.filter(
            camera=camera,
            day=selected_day,
            archived_at__isnull=True,
        )
        .values_list("model_per_color", flat=True)
        .first()
        or {}
    )
    active_counts = {
        str(color).strip().lower(): int(count)
        for color, count in model_per_color.items()
        if str(color).strip() and int(count) > 0
    }
    if not active_counts:
        return {}

    consumed: Counter[str] = Counter()
    remaining_events = sum(active_counts.values())
    brand_counts: dict[str, Counter[str]] = defaultdict(Counter)
    events = (
        AlwaysOnImportedEvent.objects.filter(
            camera=camera,
            mode="always_on",
            applied_to_analytics=True,
            occurred_at__gte=selected_start,
            occurred_at__lt=selected_end,
        )
        .order_by("-upstream_event_id")
        .values_list("color", "class_name", "brand")
    )
    for classified_color, class_name, classified_brand in events.iterator():
        # Keep this compatible with event_sync._event_color.
        color = (classified_color or class_name).split("_", 1)[0].strip().lower()
        if (
            not color
            or len(color) > 32
            or color not in active_counts
            or consumed[color] >= active_counts[color]
        ):
            continue
        # Unknown brands still consume their event's place in the active tail;
        # otherwise an older, already archived known brand could leak in.
        consumed[color] += 1
        remaining_events -= 1

        # Keep this compatible with event_sync._event_brand.  Explicit
        # ``unknown`` and legacy ``unclassified`` values are evidence that no
        # brand was identified, so they cannot become a dominant brand.
        if classified_brand is not None:
            brand = " ".join(classified_brand.split()).lower()
            if brand and len(brand) <= 100 and brand not in NON_DOMINANT_BRANDS:
                brand_counts[color][brand] += 1
        if remaining_events == 0:
            break

    return {
        color: (
            min(
                brand_counts[color].items(),
                key=lambda item: (-item[1], item[0]),
            )[0]
            if consumed[color] == active_counts[color] and brand_counts[color]
            else None
        )
        for color in sorted(active_counts)
    }


def production_payload(camera: str, day: date | str | None = None) -> dict:
    camera = ai.normalize(camera)
    selected_day = _selected_day(day)
    now = timezone.now()
    close_stale_runs(now)
    current_day = business_day_for(now)

    products = list(
        Product.objects.filter(is_active=True).order_by(
            "name", "color", "weight_kg", "id"
        )
    )
    mapping_rows = list(
        AlwaysOnColorProductMapping.objects.filter(camera=camera)
        .select_related("product")
        .order_by("color")
    )
    mapping_by_color = {row.color: row for row in mapping_rows}
    observed_colors = set(
        AlwaysOnProductionRun.objects.filter(camera=camera)
        .values_list("color", flat=True)
        .distinct()
    )
    available_colors = list(BASE_COLORS)
    available_colors.extend(
        sorted((observed_colors | set(mapping_by_color)) - set(BASE_COLORS))
    )

    totals = _day_totals(camera, current_day)
    preview_colors = sorted(
        set(totals),
        key=lambda color: (
            BASE_COLORS.index(color) if color in BASE_COLORS else len(BASE_COLORS),
            color,
        ),
    )
    preview = []
    for color in preview_colors:
        mapping = mapping_by_color.get(color)
        configured = bool(mapping and mapping.product.is_active)
        preview.append(
            {
                "color": color,
                **totals[color],
                "product": mapping.product_id if mapping else None,
                "product_label": str(mapping.product) if mapping else None,
                "configured": configured,
            }
        )

    runs = list(
        AlwaysOnProductionRun.objects.filter(camera=camera).order_by(
            "-started_at", "-id"
        )[:100]
    )
    # ``runs`` remains the compact recent journal used by the settings view.
    # A selected analytics day must not silently lose intervals merely because
    # more than 100 newer runs exist, so it has a separate complete query.
    selected_start = (
        timezone.make_aware(
            datetime.combine(selected_day, time.min),
            _default_timezone(),
        )
        if selected_day is not None
        else None
    )
    selected_end = (
        timezone.make_aware(
            datetime.combine(selected_day + timedelta(days=1), time.min),
            _default_timezone(),
        )
        if selected_day is not None
        else None
    )
    day_runs = (
        list(
            AlwaysOnProductionRun.objects.filter(
                camera=camera,
                # Analytics chart days are calendar dates.  ``business_day``
                # is a warehouse shift marker and changes at 19:00, so using
                # it here would put a 20:00 interval under tomorrow's bar.
                # Overlap also preserves legacy rows created before runs were
                # split at local midnight.  ``gte`` keeps a legitimate first
                # bag counted exactly at 00:00 in the new calendar day.
                started_at__lt=selected_end,
                last_counted_at__gte=selected_start,
            ).order_by("started_at", "id")
        )
        if selected_day is not None
        else []
    )
    batches = list(
        AlwaysOnStockBatch.objects.filter(camera=camera)
        .prefetch_related("items__product", "items__receipt")
        .order_by("-business_day", "-id")[:31]
    )
    raw_day_runs = [
        _run_payload(
            row,
            selected_start=selected_start,
            selected_end=selected_end,
        )
        for row in day_runs
    ]
    algorithm_day_runs, run_smoothing = _run_smoothing_payload(raw_day_runs)
    return {
        "camera": camera,
        "selected_day": selected_day.isoformat() if selected_day else None,
        "dominant_brand_by_color": _dominant_brand_by_color(
            camera,
            selected_day,
            selected_start,
            selected_end,
        ),
        # The exact journal remains visible and backward-compatible.  The
        # algorithm view is derived only for selected-day analytics.
        "day_runs": raw_day_runs,
        "algorithm_day_runs": algorithm_day_runs,
        "run_smoothing": run_smoothing,
        "timezone": settings.TIME_ZONE,
        "close_time": CLOSE_TIME.strftime("%H:%M"),
        "current_business_day": current_day.isoformat(),
        "next_run_at": _iso(scheduled_for(current_day)),
        "fully_configured": all(
            color in mapping_by_color and mapping_by_color[color].product.is_active
            for color in available_colors
        ),
        "available_colors": available_colors,
        "mappings": [
            _mapping_payload(mapping_by_color.get(color), color)
            for color in available_colors
        ],
        "products": [_product_payload(product) for product in products],
        "runs": [_run_payload(row) for row in runs],
        "preview": preview,
        "batches": [_batch_payload(row) for row in batches],
    }


@transaction.atomic
def save_mappings(camera: str, mappings: list[dict], user=None) -> dict:
    camera = ai.normalize(camera)
    if not isinstance(mappings, list):
        raise ValidationError({"mappings": "Передайте список привязок"})

    normalized: list[tuple[str, int | None]] = []
    seen: set[str] = set()
    for item in mappings:
        if not isinstance(item, dict):
            raise ValidationError({"mappings": "Некорректная привязка"})
        color = _normalize_color(item.get("color"))
        if color in seen:
            raise ValidationError({"mappings": f"Цвет {color} передан повторно"})
        seen.add(color)
        product_id = item.get("product")
        if product_id is not None:
            if isinstance(product_id, bool):
                raise ValidationError({"mappings": "Некорректный товар"})
            try:
                product_id = int(product_id)
            except (TypeError, ValueError) as exc:
                raise ValidationError({"mappings": "Некорректный товар"}) from exc
            if product_id <= 0:
                raise ValidationError({"mappings": "Некорректный товар"})
        normalized.append((color, product_id))

    product_ids = {product_id for _color, product_id in normalized if product_id}
    products = Product.objects.select_for_update().filter(pk__in=product_ids).in_bulk()
    for color, product_id in normalized:
        if product_id is None:
            continue
        product = products.get(product_id)
        if product is None or not product.is_active:
            raise ValidationError(
                {
                    "mappings": f"Товар #{product_id} не найден или отключён",
                }
            )
        if color in BASE_COLORS and product.color.lower() != color:
            expected = COLOR_LABELS[color]
            raise ValidationError(
                {
                    "mappings": f"Для цвета «{expected}» выберите товар того же цвета",
                }
            )

    list(
        AlwaysOnColorProductMapping.objects.select_for_update().filter(
            camera=camera,
            color__in=seen,
        )
    )
    for color, product_id in normalized:
        if product_id is None:
            AlwaysOnColorProductMapping.objects.filter(
                camera=camera,
                color=color,
            ).delete()
            continue
        AlwaysOnColorProductMapping.objects.update_or_create(
            camera=camera,
            color=color,
            defaults={"product_id": product_id, "updated_by": user},
        )
    return production_payload(camera)


@transaction.atomic
def record_correction(
    camera: str,
    color: str,
    amount: int,
    reason: str,
    user=None,
) -> AlwaysOnProductionCorrection:
    camera = ai.normalize(camera)
    color = _normalize_color(color)
    if isinstance(amount, bool):
        amount = 0
    try:
        amount = int(amount)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"amount": "Укажите количество больше нуля"}) from exc
    if amount <= 0:
        raise ValidationError({"amount": "Укажите количество больше нуля"})
    reason = " ".join(str(reason or "").split())
    if len(reason) < 5 or len(reason) > 500:
        raise ValidationError({"reason": "Укажите причину от 5 до 500 символов"})

    # Share the same camera→batch mutex and lock order as event ingestion and
    # automatic stock posting.  Otherwise a correction could commit just
    # after the receipt totals were read but before the batch became terminal.
    AlwaysOnCounterCursor.objects.select_for_update().get_or_create(camera=camera)
    business_day = business_day_for(timezone.now())
    batch = (
        AlwaysOnStockBatch.objects.select_for_update()
        .filter(camera=camera, business_day=business_day)
        .first()
    )
    if batch is not None and batch.status in TERMINAL_BATCH_STATUSES:
        raise ValidationError({"detail": "Эта производственная смена уже закрыта"})

    detected = (
        AlwaysOnProductionRun.objects.select_for_update()
        .filter(
            camera=camera,
            business_day=business_day,
            color=color,
        )
        .aggregate(value=Sum("model_bags"))["value"]
        or 0
    )
    corrected = (
        AlwaysOnProductionCorrection.objects.select_for_update()
        .filter(
            camera=camera,
            business_day=business_day,
            color=color,
        )
        .aggregate(value=Sum("delta"))["value"]
        or 0
    )
    available = detected + corrected
    if amount > available:
        raise ValidationError(
            {
                "amount": f"Для цвета доступно только {available}",
            }
        )
    return AlwaysOnProductionCorrection.objects.create(
        camera=camera,
        business_day=business_day,
        color=color,
        delta=-amount,
        reason=reason,
        created_by=user,
    )


def _locked_or_created_batch(camera: str, business_day: date) -> AlwaysOnStockBatch:
    try:
        return AlwaysOnStockBatch.objects.select_for_update().get(
            camera=camera,
            business_day=business_day,
        )
    except AlwaysOnStockBatch.DoesNotExist:
        pass
    try:
        with transaction.atomic():
            return AlwaysOnStockBatch.objects.create(
                camera=camera,
                business_day=business_day,
                scheduled_for=scheduled_for(business_day),
            )
    except IntegrityError:
        return AlwaysOnStockBatch.objects.select_for_update().get(
            camera=camera,
            business_day=business_day,
        )


@transaction.atomic
def _post_one(camera: str, business_day: date, now: datetime) -> AlwaysOnStockBatch:
    # The event importer owns this lock before it checks a terminal batch.
    # Keep the same lock order here so count ingestion and stock closing can
    # never deadlock or let a late event slip behind an immutable receipt.
    cursor = (
        AlwaysOnCounterCursor.objects.select_for_update()
        .filter(
            camera=camera,
        )
        .first()
    )
    batch = _locked_or_created_batch(camera, business_day)
    if batch.status in TERMINAL_BATCH_STATUSES:
        return batch
    if cursor is not None and cursor.event_sync_supported is not False:
        if cursor.last_event_id is None or not cursor.event_boundary_validated:
            batch.status = AlwaysOnStockBatch.BLOCKED
            batch.last_error = "Ожидается проверка журнала событий AI"
            batch.attempts += 1
            batch.posted_at = None
            batch.save(
                update_fields=[
                    "status",
                    "last_error",
                    "attempts",
                    "posted_at",
                    "updated_at",
                ]
            )
            return batch
        required_caught_up_at = scheduled_for(business_day) + EVENT_SETTLE_DELAY
        if (
            cursor.event_sync_error
            or cursor.event_sync_failed_at is not None
            or cursor.event_caught_up_at is None
            or cursor.event_caught_up_at < required_caught_up_at
        ):
            batch.status = AlwaysOnStockBatch.BLOCKED
            batch.last_error = "Ожидается синхронизация событий AI после закрытия смены"
            batch.attempts += 1
            batch.posted_at = None
            batch.save(
                update_fields=[
                    "status",
                    "last_error",
                    "attempts",
                    "posted_at",
                    "updated_at",
                ]
            )
            return batch
    if batch.items.exists():
        # A non-terminal batch with committed receipt links cannot be retried
        # automatically without risking a duplicate warehouse movement.
        batch.status = AlwaysOnStockBatch.FAILED
        batch.last_error = "Партия содержит незавершённые складские проводки"
        batch.save(update_fields=["status", "last_error", "updated_at"])
        return batch

    batch.attempts += 1
    # No production run may remain active once its shift is being posted.
    for row in AlwaysOnProductionRun.objects.select_for_update().filter(
        camera=camera,
        business_day=business_day,
        ended_at__isnull=True,
    ):
        row.ended_at = row.last_counted_at
        row.save(update_fields=["ended_at", "updated_at"])
    list(
        AlwaysOnProductionCorrection.objects.select_for_update().filter(
            camera=camera,
            business_day=business_day,
        )
    )
    totals = _day_totals(camera, business_day)
    invalid = [color for color, values in totals.items() if values["net_bags"] < 0]
    if invalid:
        batch.status = AlwaysOnStockBatch.FAILED
        batch.last_error = "Коррекции превысили выпуск: " + ", ".join(invalid)
        batch.save(update_fields=["status", "last_error", "attempts", "updated_at"])
        return batch

    positive = {
        color: values for color, values in totals.items() if values["net_bags"] > 0
    }
    if not positive:
        batch.status = AlwaysOnStockBatch.EMPTY
        batch.total_bags = 0
        batch.last_error = ""
        batch.posted_at = now
        batch.save(
            update_fields=[
                "status",
                "total_bags",
                "last_error",
                "attempts",
                "posted_at",
                "updated_at",
            ]
        )
        log_event(
            "always_on_stock_posted",
            f"AI 24/7 · {camera}: смена {business_day:%d.%m.%Y} закрыта без прихода",
            payload={
                "batch": batch.pk,
                "camera": camera,
                "business_day": business_day.isoformat(),
                "total_bags": 0,
                "status": batch.status,
            },
        )
        return batch

    mapping_rows = list(
        AlwaysOnColorProductMapping.objects.select_for_update()
        .filter(camera=camera, color__in=positive)
        .select_related("product")
    )
    mapping_by_color = {row.color: row for row in mapping_rows}
    missing = sorted(
        color
        for color in positive
        if color not in mapping_by_color
        or not mapping_by_color[color].product.is_active
    )
    if missing:
        error = "Не настроен товар для цветов: " + ", ".join(missing)
        should_log = (
            batch.status != AlwaysOnStockBatch.BLOCKED or batch.last_error != error
        )
        batch.status = AlwaysOnStockBatch.BLOCKED
        batch.total_bags = sum(values["net_bags"] for values in positive.values())
        batch.last_error = error
        batch.posted_at = None
        batch.save(
            update_fields=[
                "status",
                "total_bags",
                "last_error",
                "attempts",
                "posted_at",
                "updated_at",
            ]
        )
        if should_log:
            log_event(
                "always_on_stock_blocked",
                f"AI 24/7 · {camera}: приход за {business_day:%d.%m.%Y} ожидает настройки",
                payload={
                    "batch": batch.pk,
                    "camera": camera,
                    "business_day": business_day.isoformat(),
                    "missing_colors": missing,
                },
            )
        return batch

    # Lock the exact catalogue rows whose active state was just validated.
    product_ids = {mapping_by_color[color].product_id for color in positive}
    locked_products = (
        Product.objects.select_for_update().filter(pk__in=product_ids).in_bulk()
    )
    newly_inactive = sorted(
        color
        for color in positive
        if not locked_products.get(mapping_by_color[color].product_id)
        or not locked_products[mapping_by_color[color].product_id].is_active
    )
    if newly_inactive:
        batch.status = AlwaysOnStockBatch.BLOCKED
        batch.last_error = "Товар отключён для цветов: " + ", ".join(newly_inactive)
        batch.save(update_fields=["status", "last_error", "attempts", "updated_at"])
        return batch

    posted_items = []
    total_bags = 0
    for color in sorted(positive):
        values = positive[color]
        product = locked_products[mapping_by_color[color].product_id]
        note = f"AI 24/7 · {camera} · смена {business_day.isoformat()} · цвет {color}"
        receipt = receive_stock(
            product,
            values["net_bags"],
            user=None,
            note=note,
        )
        posting = AlwaysOnStockPosting.objects.create(
            batch=batch,
            color=color,
            product=product,
            detected_bags=values["detected_bags"],
            correction_bags=values["correction_bags"],
            posted_bags=values["net_bags"],
            receipt=receipt,
        )
        posted_items.append(posting)
        total_bags += values["net_bags"]

    batch.status = AlwaysOnStockBatch.POSTED
    batch.total_bags = total_bags
    batch.last_error = ""
    batch.posted_at = now
    batch.save(
        update_fields=[
            "status",
            "total_bags",
            "last_error",
            "attempts",
            "posted_at",
            "updated_at",
        ]
    )
    log_event(
        "always_on_stock_posted",
        f"AI 24/7 · {camera}: {total_bags} мешков добавлено на склад",
        payload={
            "batch": batch.pk,
            "camera": camera,
            "business_day": business_day.isoformat(),
            "total_bags": total_bags,
            "items": [
                {
                    "color": item.color,
                    "product": item.product_id,
                    "bags": item.posted_bags,
                    "receipt": item.receipt_id,
                }
                for item in posted_items
            ],
        },
    )
    return batch


def _mark_failed(
    camera: str, business_day: date, error: Exception
) -> AlwaysOnStockBatch:
    with transaction.atomic():
        batch = _locked_or_created_batch(camera, business_day)
        if batch.status in TERMINAL_BATCH_STATUSES:
            return batch
        batch.status = AlwaysOnStockBatch.FAILED
        batch.last_error = str(error)[:500] or error.__class__.__name__
        batch.attempts += 1
        batch.save(update_fields=["status", "last_error", "attempts", "updated_at"])
        return batch


def _due_pairs(now: datetime) -> list[tuple[str, date]]:
    current_day = business_day_for(now)
    terminal = AlwaysOnStockBatch.objects.filter(
        camera=OuterRef("camera"),
        business_day=OuterRef("business_day"),
        status__in=TERMINAL_BATCH_STATUSES,
    )
    pairs = set(
        AlwaysOnProductionRun.objects.filter(business_day__lt=current_day)
        .annotate(_terminal=Exists(terminal))
        .filter(_terminal=False)
        .order_by()
        .values_list("camera", "business_day")
        .distinct()
    )
    pairs.update(
        AlwaysOnStockBatch.objects.filter(
            business_day__lt=current_day,
            status__in=(
                AlwaysOnStockBatch.SCHEDULED,
                AlwaysOnStockBatch.BLOCKED,
                AlwaysOnStockBatch.FAILED,
            ),
        ).values_list("camera", "business_day")
    )
    return sorted(pairs, key=lambda item: (item[1], item[0]))


def post_due_stock(now: datetime | None = None) -> list[dict]:
    """Post every overdue camera/day independently and retry safe failures."""

    now = _aware(now)
    close_stale_runs(now)
    result = []
    for camera, business_day in _due_pairs(now):
        try:
            batch = _post_one(camera, business_day, now)
        except Exception as exc:
            log.exception(
                "AI 24/7 stock posting failed camera=%s day=%s",
                camera,
                business_day,
            )
            batch = _mark_failed(camera, business_day, exc)
        result.append(_batch_payload(batch))
    return result


def retry_batch(batch_id: int) -> dict:
    try:
        batch = AlwaysOnStockBatch.objects.get(pk=batch_id)
    except AlwaysOnStockBatch.DoesNotExist as exc:
        raise NotFound("Складская партия не найдена") from exc
    if batch.status in TERMINAL_BATCH_STATUSES:
        return _batch_payload(batch)
    now = timezone.now()
    try:
        batch = _post_one(batch.camera, batch.business_day, now)
    except Exception as exc:
        log.exception("AI 24/7 manual stock retry failed batch=%s", batch_id)
        batch = _mark_failed(batch.camera, batch.business_day, exc)
    return _batch_payload(batch)
