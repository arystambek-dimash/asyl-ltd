"""Read-only production projections and display smoothing; no ledger mutations."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime

from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product
from apps.warehouse.models import StockItem, Warehouse

from . import ai
from .analytics import color_payload
from .color_resolution import (
    PENDING_COLORS,
    effective_brand,
    effective_color_key,
    merge_inferred,
    overlay_daily_rows,
    resolved_day_runs,
    with_inferred,
)
from .event_protocol import normalize_brand
from .models import (
    ANALYTICS_SCOPE_AI247,
    AlwaysOnColorProductMapping,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    AlwaysOnProductionRun,
    AlwaysOnStockBatch,
    AlwaysOnStockPosting,
)
from .production_catalog import (
    BASE_COLORS,
    _warehouse_for_camera,
)
from .production_runs import (
    CLOSE_TIME,
    _day_totals,
    _iso,
    business_day_for,
    effective_ended_at,
    local_day_window,
    scheduled_for,
)

RUN_SMOOTHING_N_MIN = 10


def _product_payload(product: Product, *, warehouse_ids: set[int] | None = None) -> dict:
    return {
        "id": product.pk,
        "label": str(product),
        "color": product.color,
        "color_label": product.get_color_display(),
        "weight_kg": str(product.weight_kg),
        "warehouse_ids": sorted(warehouse_ids or ()),
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
    now: datetime,
    selected_start: datetime,
    selected_end: datetime,
) -> dict:
    ended_at = effective_ended_at(row, now)
    starts_before_day = row.started_at < selected_start
    ends_after_day = row.last_counted_at >= selected_end
    return {
        "id": row.pk,
        "camera": row.camera,
        "business_day": row.business_day.isoformat(),
        "color": row.color,
        "started_at": _iso(row.started_at),
        "last_counted_at": _iso(row.last_counted_at),
        "ended_at": _iso(ended_at),
        "model_bags": row.model_bags,
        "is_approximate": row.is_approximate,
        "status": "active" if ended_at is None else "closed",
        "starts_before_day": starts_before_day,
        "ends_after_day": ends_after_day,
        "is_partial_for_day": starts_before_day or ends_after_day,
    }


def _is_run_smoothing_barrier(run: dict) -> bool:
    """Return whether a display run must remain an exact, isolated anchor.

    A partial legacy run carries the bag count for the whole cross-midnight
    interval rather than only the selected calendar day.  Approximate rows do
    not preserve an authoritative colour sequence either.  Neither is safe to
    merge or use as a sandwich neighbour. Explicitly missing classifications
    are also barriers: smoothing must not turn unknown evidence into a colour.
    """

    return bool(
        run.get("is_partial_for_day")
        or run.get("is_approximate")
        or run.get("color") in {"unclassified", "unknown"}
    )


def _merge_algorithm_runs(left: dict, right: dict) -> dict:
    """Combine adjacent display runs without mutating either source payload."""

    merged = dict(left)
    merged["model_bags"] = int(left["model_bags"]) + int(right["model_bags"])
    inferred = merge_inferred(
        {"_": left.get("inferred") or {}}, {"_": right.get("inferred") or {}}
    ).get("_")
    if inferred:
        merged["inferred"] = inferred
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


def smooth_day_runs(raw_runs: list[dict]) -> list[dict]:
    """Return the supplied sandwich smoother as a read-only display view.

    Normal runs follow the operator-provided algorithm exactly: adjacent equal
    colours are first coalesced; then the smallest unlocked run below
    ``RUN_SMOOTHING_N_MIN`` is recoloured only when both neighbours have the
    same other colour.  A run at an edge or party boundary is locked and
    retained.

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
            and int(run["model_bags"]) < RUN_SMOOTHING_N_MIN
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

    def cards(per_color: dict[str, int], runs: list[dict]) -> list[dict]:
        # Resolved bags keep their «по соседям»/«по голосам» marker on the
        # selected-day colour cards, exactly as on their run rows.
        return with_inferred(
            color_payload(per_color),
            "color",
            [{str(run["color"]): run["inferred"]} for run in runs if run.get("inferred")],
        )

    metadata = {
        "n_min": RUN_SMOOTHING_N_MIN,
        "raw_model_total": sum(raw_per_color.values()),
        "algorithm_model_total": sum(algorithm_per_color.values()),
        "raw_colors": cards(raw_per_color, raw_runs),
        "algorithm_colors": cards(algorithm_per_color, algorithm_runs),
    }
    return algorithm_runs, metadata


def _posting_payload(row: AlwaysOnStockPosting) -> dict:
    return {
        "id": row.pk,
        "kind": row.kind,
        "color": row.color,
        "product": row.product_id,
        "product_label": str(row.product),
        "detected_bags": row.detected_bags,
        "resolved_bags": row.resolved_bags,
        "correction_bags": row.correction_bags,
        "posted_bags": row.posted_bags,
        "receipt_id": row.receipt_id,
    }


def _batch_payload(row: AlwaysOnStockBatch) -> dict:
    items = getattr(row, "posting_items", None)
    if items is None:
        # Single-batch command responses do not arrive through the list query.
        items = row.items.select_related("product").all()
    return {
        "id": row.pk,
        "camera": row.camera,
        "warehouse": row.warehouse_id,
        "warehouse_name": row.warehouse.name,
        "business_day": row.business_day.isoformat(),
        "scheduled_for": _iso(row.scheduled_for),
        "status": row.status,
        "total_bags": row.total_bags,
        # Bags left without a colour at posting; «Указать цвет» receives them.
        "pending_bags": row.pending_bags,
        "last_error": row.last_error,
        "attempts": row.attempts,
        "items": [_posting_payload(item) for item in items],
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
    selected_day: date,
    selected_start: datetime,
    selected_end: datetime,
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

    daily = (
        AlwaysOnDailyAnalytics.objects.filter(
            camera=camera,
            day=selected_day,
            archived_at__isnull=True,
        )
        .only("camera", "day", "model_total", "model_per_color")
        .first()
    )
    if daily is None:
        return {}
    # Resolved bags count under their colour here exactly as in the totals.
    overlay_daily_rows([daily])
    model_per_color = daily.model_per_color or {}
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
            analytics_scope=ANALYTICS_SCOPE_AI247,
            mode="always_on",
            applied_to_analytics=True,
            applied_to_production=True,
            occurred_at__gte=selected_start,
            occurred_at__lt=selected_end,
        )
        .order_by("-upstream_event_id")
        .values_list(
            "color",
            "class_name",
            "brand",
            "resolved_color",
            "color_resolution",
            "resolved_brand",
            "brand_resolution",
        )
    )
    for (
        classified_color,
        class_name,
        classified_brand,
        resolved_color,
        color_method,
        resolved_brand,
        brand_method,
    ) in events.iterator():
        # Same colour/brand the overlaid daily totals and stock posting use.
        color = effective_color_key(
            classified_color, class_name, resolved_color, color_method
        )
        classified_brand = effective_brand(
            classified_brand, resolved_brand, brand_method
        )
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

        # Explicit ``unknown`` and legacy ``unclassified`` values are evidence
        # that no brand was identified, so they cannot become a dominant brand.
        brand = normalize_brand(classified_brand)
        if brand is not None:
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


def _selected_day_section(
    camera: str, selected_day: date | None, *, now: datetime
) -> dict:
    """Runs of one calendar day behind a chart bar; empty without a day."""

    if selected_day is None:
        return {
            "dominant_brand_by_color": {},
            "day_runs": [],
            "algorithm_day_runs": [],
            "run_smoothing": _run_smoothing_payload([])[1],
        }
    selected_start, selected_end = local_day_window(selected_day)
    day_runs = list(
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
    # Bags the camera left as ``unknown`` show their resolved colour with a
    # marker; the stored run ledger keeps the camera's answer.
    raw_day_runs = resolved_day_runs(
        camera,
        day_runs,
        [
            _run_payload(
                row,
                now=now,
                selected_start=selected_start,
                selected_end=selected_end,
            )
            for row in day_runs
        ],
        start=selected_start,
        end=selected_end,
    )
    algorithm_day_runs, run_smoothing = _run_smoothing_payload(raw_day_runs)
    return {
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
    }


def production_payload(camera: str, day: date | str | None = None) -> dict:
    camera = ai.normalize(camera)
    selected_day = _selected_day(day)
    now = timezone.now()
    current_day = business_day_for(now)

    warehouse = _warehouse_for_camera(
        camera,
        require_active=False,
    )
    warehouses = list(Warehouse.objects.filter(is_active=True).order_by("name", "id"))
    if warehouse.pk not in {row.pk for row in warehouses}:
        warehouses.append(warehouse)

    products = list(
        Product.objects.filter(is_active=True).order_by(
            "name", "color", "weight_kg", "id"
        )
    )
    stock_rows = StockItem.objects.filter(
        product_id__in=[p.pk for p in products]
    ).values_list("product_id", "warehouse_id")
    stocked_products: set[int] = set()
    warehouses_by_product: dict[int, set[int]] = defaultdict(set)
    for product_id, warehouse_id in stock_rows:
        warehouses_by_product[product_id].add(warehouse_id)
        if warehouse_id == warehouse.pk:
            stocked_products.add(product_id)
    mapping_rows = list(
        AlwaysOnColorProductMapping.objects.filter(camera=camera)
        .select_related("product")
        .order_by("color")
    )
    mapping_by_color = {row.color: row for row in mapping_rows}

    def is_configured(mapping: AlwaysOnColorProductMapping | None) -> bool:
        # ``stocked_products`` holds only active products stocked in the
        # camera's warehouse.
        return mapping is not None and mapping.product_id in stocked_products

    observed_colors = set(
        AlwaysOnProductionRun.objects.filter(camera=camera)
        .order_by()
        .values_list("color", flat=True)
        .distinct()
    )
    available_colors = list(BASE_COLORS)
    # ``unknown`` bags are resolved by neighbours/votes or assigned manually;
    # they never map to a product (that used to block the whole shift).
    available_colors.extend(
        sorted(
            (observed_colors | set(mapping_by_color))
            - set(BASE_COLORS)
            - PENDING_COLORS
        )
    )

    totals = _day_totals(camera, current_day)
    unresolved_bags = sum(
        max(0, totals[color]["net_bags"]) for color in PENDING_COLORS if color in totals
    )
    preview_colors = sorted(
        set(totals) - PENDING_COLORS,
        key=lambda color: (
            BASE_COLORS.index(color) if color in BASE_COLORS else len(BASE_COLORS),
            color,
        ),
    )
    preview = []
    for color in preview_colors:
        mapping = mapping_by_color.get(color)
        preview.append(
            {
                "color": color,
                **totals[color],
                "product": mapping.product_id if mapping else None,
                "product_label": str(mapping.product) if mapping else None,
                "configured": is_configured(mapping),
            }
        )

    batches = list(
        AlwaysOnStockBatch.objects.filter(camera=camera)
        .select_related("warehouse")
        .prefetch_related(
            Prefetch(
                "items",
                queryset=AlwaysOnStockPosting.objects.select_related("product"),
                to_attr="posting_items",
            )
        )
        .order_by("-business_day", "-id")[:31]
    )
    return {
        "camera": camera,
        "warehouse": warehouse.pk,
        "warehouse_name": warehouse.name,
        "warehouses": [
            {
                "id": row.pk,
                "code": row.code,
                "name": row.name,
                "is_active": row.is_active,
                "is_default": row.is_default,
            }
            for row in warehouses
        ],
        "selected_day": selected_day.isoformat() if selected_day else None,
        **_selected_day_section(camera, selected_day, now=now),
        "timezone": settings.TIME_ZONE,
        "close_time": CLOSE_TIME.strftime("%H:%M"),
        "current_business_day": current_day.isoformat(),
        "next_run_at": _iso(scheduled_for(current_day)),
        "fully_configured": all(
            is_configured(mapping_by_color.get(color)) for color in available_colors
        ),
        "available_colors": available_colors,
        "mappings": [
            _mapping_payload(mapping_by_color.get(color), color)
            for color in available_colors
        ],
        "products": [
            _product_payload(
                product,
                warehouse_ids=warehouses_by_product.get(product.pk),
            )
            for product in products
        ],
        "preview": preview,
        # Current shift bags still without a colour: posted later, never
        # blocking. «Указать цвет» assigns them (production.assign_unknown_color).
        "unresolved": {
            "business_day": current_day.isoformat(),
            "bags": unresolved_bags,
        },
        "batches": [_batch_payload(row) for row in batches],
    }
