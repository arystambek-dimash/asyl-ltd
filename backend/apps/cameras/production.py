"""Commands for production configuration, corrections and atomic stock posting.

Read projections, warehouse rules and the run ledger live in their respective
focused modules (production_queries, production_catalog, production_runs).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.catalog.models import Product
from apps.eventlog.services import log_event
from apps.warehouse.models import StockItem, Warehouse
from apps.warehouse.services import get_main_warehouse, lock_stock_item, receive_stock

from . import ai, color_resolution
from .models import (
    ANALYTICS_SCOPE_AI247,
    METHOD_MANUAL,
    AlwaysOnColorProductMapping,
    AlwaysOnCounterCursor,
    AlwaysOnImportedEvent,
    AlwaysOnProductionCorrection,
    AlwaysOnProductionRun,
    AlwaysOnStockBatch,
    AlwaysOnStockPosting,
    AlwaysOnWarehouseRoute,
    ContinuousCameraRole,
)
from .policies import assert_contour_camera
from .production_catalog import (
    BASE_COLORS,
    color_label,
    _warehouse_for_camera,
)
from .production_queries import (
    _batch_payload,
    _selected_day,
    production_payload,
)
from .production_runs import (
    TERMINAL_BATCH_STATUSES,
    _aware,
    _day_totals,
    _normalize_color,
    business_day_for,
    close_stale_runs,
    scheduled_for,
)

log = logging.getLogger(__name__)
# Keep a clean post-cutoff journal observation before making stock immutable.
EVENT_SETTLE_DELAY = timedelta(minutes=1)


def _camera_has_unposted_production(camera: str) -> bool:
    # A posting attempt can create/pin a batch before the event journal has
    # caught up and before any positive run is visible. That non-terminal
    # snapshot still belongs to the current route and must fence route changes.
    if (
        AlwaysOnStockBatch.objects.filter(camera=camera)
        .exclude(status__in=TERMINAL_BATCH_STATUSES)
        .exists()
    ):
        return True
    production_days = set(
        AlwaysOnProductionRun.objects.filter(camera=camera, model_bags__gt=0)
        .order_by()
        .values_list("business_day", flat=True)
        .distinct()
    )
    if not production_days:
        return False
    terminal_days = set(
        AlwaysOnStockBatch.objects.filter(
            camera=camera,
            business_day__in=production_days,
            status__in=TERMINAL_BATCH_STATUSES,
        ).values_list("business_day", flat=True)
    )
    return bool(production_days - terminal_days)


@transaction.atomic
def save_mappings(
    camera: str,
    mappings: list[dict],
    user=None,
    *,
    warehouse_id: int | None = None,
) -> dict:
    """Route the camera to a warehouse and bind its colours to products.

    ``mappings`` are rows validated by AlwaysOnProductMappingsSerializer:
    normalized unique colours and ``product`` as a positive id or ``None``.
    """

    camera = ai.normalize(camera)
    # Event ingestion and stock posting take this per-camera mutex first.
    # Taking the same lock before reading runs/batches closes the window where
    # a page from the old route is in flight but not committed yet.
    AlwaysOnCounterCursor.locked(camera)
    # Warehouse configuration and transfers always lock the stable main row
    # first. Follow the same order here, then lock current/selected ids in
    # ascending order; a secondary -> main route change must not invert it.
    main_warehouse = get_main_warehouse(lock=True)
    route = (
        AlwaysOnWarehouseRoute.objects.select_for_update().filter(camera=camera).first()
    )
    current_warehouse_id = route.warehouse_id if route else main_warehouse.pk
    selected_warehouse_id = (
        current_warehouse_id if warehouse_id is None else warehouse_id
    )

    locked_warehouses = {main_warehouse.pk: main_warehouse}
    locked_warehouses.update(
        {
            row.pk: row
            for row in Warehouse.objects.select_for_update(no_key=True)
            .filter(pk__in={current_warehouse_id, selected_warehouse_id})
            .order_by("pk")
        }
    )
    current_warehouse = locked_warehouses[current_warehouse_id]
    selected_warehouse = locked_warehouses.get(selected_warehouse_id)
    if selected_warehouse is None:
        raise ValidationError(
            {
                "warehouse": "Склад не найден",
                "code": "warehouse_not_found",
            }
        )
    if (
        not selected_warehouse.is_active
        and selected_warehouse.pk != current_warehouse.pk
    ):
        raise ValidationError(
            {
                "warehouse": "Выберите действующий склад",
                "code": "warehouse_inactive",
            }
        )
    if (
        selected_warehouse.pk != current_warehouse.pk
        and _camera_has_unposted_production(camera)
    ):
        raise ValidationError(
            {
                "warehouse": (
                    "Склад нельзя менять, пока у камеры есть "
                    "неоприходованная производственная смена"
                ),
                "code": "warehouse_has_unposted_production",
            }
        )

    normalized = [(item["color"], item["product"]) for item in mappings]
    product_ids = {product_id for _color, product_id in normalized if product_id}
    products = (
        Product.objects.select_for_update()
        .filter(pk__in=product_ids)
        .order_by("pk")
        .in_bulk()
    )
    stock_by_product = {
        row.product_id: row
        for row in StockItem.objects.select_for_update(of=("self",))
        .filter(warehouse=selected_warehouse, product_id__in=product_ids)
        .select_related("warehouse")
        .order_by("product_id")
    }
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
            expected = color_label(color)
            raise ValidationError(
                {
                    "mappings": f"Для цвета «{expected}» выберите товар того же цвета",
                }
            )
    # Persist a stock card for this exact route. The same catalogue product may
    # be produced and held in several warehouses independently.
    for product_id in sorted(product_ids):
        if product_id not in stock_by_product:
            stock_by_product[product_id] = lock_stock_item(
                products[product_id],
                selected_warehouse,
                require_active=False,
            )

    AlwaysOnWarehouseRoute.objects.update_or_create(
        camera=camera,
        defaults={
            "warehouse": selected_warehouse,
            "updated_by": user,
        },
    )

    list(
        AlwaysOnColorProductMapping.objects.select_for_update().filter(
            camera=camera,
            color__in=[color for color, _product_id in normalized],
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


def _locked_or_created_batch(
    camera: str,
    business_day: date,
    *,
    warehouse: Warehouse | None = None,
) -> AlwaysOnStockBatch:
    warehouse = warehouse or _warehouse_for_camera(
        camera,
        lock=True,
        require_active=False,
    )
    batch = (
        AlwaysOnStockBatch.objects.select_for_update()
        .filter(camera=camera, business_day=business_day)
        .first()
    )
    if batch is not None:
        return batch
    try:
        with transaction.atomic():
            return AlwaysOnStockBatch.objects.create(
                camera=camera,
                warehouse=warehouse,
                business_day=business_day,
                scheduled_for=scheduled_for(business_day),
            )
    except IntegrityError:
        return AlwaysOnStockBatch.objects.select_for_update().get(
            camera=camera,
            business_day=business_day,
        )


def _save_batch_state(
    batch: AlwaysOnStockBatch,
    status: str,
    error: str = "",
    **fields,
) -> AlwaysOnStockBatch:
    """Persist a batch transition together with the attempt counter."""

    batch.status = status
    batch.last_error = error
    for name, value in fields.items():
        setattr(batch, name, value)
    batch.save(
        update_fields=["status", "last_error", "attempts", *fields, "updated_at"]
    )
    return batch


def _event_journal_wait(
    cursor: AlwaysOnCounterCursor, camera: str, business_day: date
) -> str:
    """Why the shift must wait for the AI event journal, or ``""``."""

    if cursor.last_event_id is None or not cursor.event_boundary_validated:
        return "Ожидается проверка журнала событий AI"
    required_caught_up_at = scheduled_for(business_day) + EVENT_SETTLE_DELAY
    if (
        cursor.has_sync_failure
        or cursor.event_caught_up_at is None
        or cursor.event_caught_up_at < required_caught_up_at
        # A bag without a colour just before 19:00 may take it from the
        # first bags after it; the final pass in _post_one freezes it.
        or color_resolution.awaits_following_bags(
            camera, business_day, cursor.event_caught_up_at
        )
    ):
        return "Ожидается синхронизация событий AI после закрытия смены"
    return ""


def _log_stock_posted(
    batch: AlwaysOnStockBatch,
    camera: str,
    business_day: date,
    message: str,
    **extra,
) -> None:
    log_event(
        "always_on_stock_posted",
        f"AI 24/7 · {camera}: {message}" + _pending_suffix(batch.pending_bags),
        payload={
            "batch": batch.pk,
            "camera": camera,
            "warehouse": batch.warehouse_id,
            "business_day": business_day.isoformat(),
            "total_bags": batch.total_bags,
            "pending_bags": batch.pending_bags,
            **extra,
        },
    )


@transaction.atomic
def _post_one(camera: str, business_day: date, now: datetime) -> AlwaysOnStockBatch:
    assert_contour_camera(camera, ANALYTICS_SCOPE_AI247)
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
    route_warehouse = _warehouse_for_camera(
        camera,
        lock=True,
        require_active=False,
    )
    batch = _locked_or_created_batch(
        camera,
        business_day,
        warehouse=route_warehouse,
    )
    if batch.status in TERMINAL_BATCH_STATUSES:
        return batch
    if cursor is not None and cursor.event_sync_supported is not False:
        waiting = _event_journal_wait(cursor, camera, business_day)
        if waiting:
            batch.attempts += 1
            return _save_batch_state(
                batch, AlwaysOnStockBatch.BLOCKED, waiting, posted_at=None
            )
    if batch.items.exists():
        # A non-terminal batch with committed receipt links cannot be retried
        # automatically without risking a duplicate warehouse movement.
        return _save_batch_state(
            batch,
            AlwaysOnStockBatch.FAILED,
            "Партия содержит незавершённые складские проводки",
        )

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
    # Authoritative neighbour/vote pass over the whole shift while its journal
    # is caught up and the cursor lock keeps new pages out.
    _resolve_shift_colors(camera, business_day)
    totals = _day_totals(camera, business_day)
    invalid = [color for color, values in totals.items() if values["net_bags"] < 0]
    if invalid:
        return _save_batch_state(
            batch,
            AlwaysOnStockBatch.FAILED,
            "Коррекции превысили выпуск: " + ", ".join(invalid),
        )

    # Bags still without a colour never block the shift: they stay pending on
    # the batch until an operator assigns them (assign_unknown_color).
    pending_bags = sum(
        totals[color]["net_bags"]
        for color in color_resolution.PENDING_COLORS
        if color in totals
    )
    positive = {
        color: values
        for color, values in totals.items()
        if values["net_bags"] > 0 and color not in color_resolution.PENDING_COLORS
    }
    if not positive:
        _save_batch_state(
            batch,
            AlwaysOnStockBatch.EMPTY,
            total_bags=0,
            pending_bags=pending_bags,
            posted_at=now,
        )
        _log_stock_posted(
            batch,
            camera,
            business_day,
            f"смена {business_day:%d.%m.%Y} закрыта без прихода",
            status=batch.status,
        )
        return batch

    # FOR UPDATE also locks the joined (NOT NULL, PROTECT) product rows, so
    # the active state checked here holds until the receipts are written.
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
        _save_batch_state(
            batch,
            AlwaysOnStockBatch.BLOCKED,
            error,
            total_bags=sum(values["net_bags"] for values in positive.values()),
            # Shown next to the error so they can be assigned before the retry.
            pending_bags=pending_bags,
            posted_at=None,
        )
        if should_log:
            log_event(
                "always_on_stock_blocked",
                (
                    f"AI 24/7 · {camera}: приход за "
                    f"{business_day:%d.%m.%Y} ожидает настройки"
                ),
                payload={
                    "batch": batch.pk,
                    "camera": camera,
                    "business_day": business_day.isoformat(),
                    "missing_colors": missing,
                },
            )
        return batch

    posted_items = []
    total_bags = 0
    for color in sorted(positive):
        values = positive[color]
        product = mapping_by_color[color].product
        note = f"AI 24/7 · {camera} · смена {business_day.isoformat()} · цвет {color}"
        receipt = receive_stock(
            product,
            values["net_bags"],
            user=None,
            note=note,
            warehouse=batch.warehouse,
            require_active=False,
        )
        posting = AlwaysOnStockPosting.objects.create(
            batch=batch,
            color=color,
            product=product,
            detected_bags=values["detected_bags"],
            resolved_bags=values["resolved_bags"],
            correction_bags=values["correction_bags"],
            posted_bags=values["net_bags"],
            receipt=receipt,
        )
        posted_items.append(posting)
        total_bags += values["net_bags"]

    _save_batch_state(
        batch,
        AlwaysOnStockBatch.POSTED,
        total_bags=total_bags,
        pending_bags=pending_bags,
        posted_at=now,
    )
    _log_stock_posted(
        batch,
        camera,
        business_day,
        f"{total_bags} мешков добавлено на склад",
        items=[
            {
                "color": item.color,
                "product": item.product_id,
                "bags": item.posted_bags,
                "resolved_bags": item.resolved_bags,
                "receipt": item.receipt_id,
            }
            for item in posted_items
        ],
    )
    return batch


def _resolve_shift_colors(camera: str, business_day: date) -> None:
    """Best-effort final resolution pass: it must never hold the shift back.

    On failure only its savepoint is rolled back; decisions already taken at
    import still count and the remaining bags wait for «Указать цвет».
    """

    try:
        with transaction.atomic():
            color_resolution.resolve_business_day(camera, business_day)
    except Exception:
        log.exception(
            "AI 24/7 unknown-bag resolution before posting failed camera=%s day=%s",
            camera,
            business_day,
        )


def _pending_suffix(pending_bags: int) -> str:
    return f"; цвет не определён: {pending_bags} меш." if pending_bags else ""


def _mark_failed(
    camera: str, business_day: date, error: Exception
) -> AlwaysOnStockBatch:
    with transaction.atomic():
        batch = _locked_or_created_batch(camera, business_day)
        if batch.status in TERMINAL_BATCH_STATUSES:
            return batch
        batch.attempts += 1
        return _save_batch_state(
            batch,
            AlwaysOnStockBatch.FAILED,
            str(error)[:500] or error.__class__.__name__,
        )


def _due_pairs(now: datetime) -> list[tuple[str, date]]:
    current_day = business_day_for(now)
    ai247_cameras = ContinuousCameraRole.objects.filter(
        analytics_scope=ANALYTICS_SCOPE_AI247,
    ).values("camera")
    terminal = AlwaysOnStockBatch.objects.filter(
        camera=OuterRef("camera"),
        business_day=OuterRef("business_day"),
        status__in=TERMINAL_BATCH_STATUSES,
    )
    pairs = set(
        AlwaysOnProductionRun.objects.filter(
            business_day__lt=current_day,
            camera__in=ai247_cameras,
        )
        .annotate(_terminal=Exists(terminal))
        .filter(_terminal=False)
        .order_by()
        .values_list("camera", "business_day")
        .distinct()
    )
    pairs.update(
        AlwaysOnStockBatch.objects.filter(
            business_day__lt=current_day,
            camera__in=ai247_cameras,
            status__in=(
                AlwaysOnStockBatch.SCHEDULED,
                AlwaysOnStockBatch.BLOCKED,
                AlwaysOnStockBatch.FAILED,
            ),
        ).values_list("camera", "business_day")
    )
    return sorted(pairs, key=lambda item: (item[1], item[0]))


def _post_or_mark_failed(
    camera: str, business_day: date, now: datetime
) -> AlwaysOnStockBatch:
    try:
        return _post_one(camera, business_day, now)
    except Exception as exc:
        log.exception(
            "AI 24/7 stock posting failed camera=%s day=%s",
            camera,
            business_day,
        )
        return _mark_failed(camera, business_day, exc)


def post_due_stock(now: datetime | None = None) -> list[dict]:
    """Post every overdue camera/day independently and retry safe failures."""

    now = _aware(now)
    close_stale_runs(now)
    return [
        _batch_payload(_post_or_mark_failed(camera, business_day, now))
        for camera, business_day in _due_pairs(now)
    ]


def retry_batch(batch_id: int) -> dict:
    try:
        batch = AlwaysOnStockBatch.objects.get(pk=batch_id)
    except AlwaysOnStockBatch.DoesNotExist as exc:
        raise NotFound("Складская партия не найдена") from exc
    assert_contour_camera(batch.camera, ANALYTICS_SCOPE_AI247)
    if batch.status in TERMINAL_BATCH_STATUSES:
        return _batch_payload(batch)
    return _batch_payload(
        _post_or_mark_failed(batch.camera, batch.business_day, timezone.now())
    )


@transaction.atomic
def assign_unknown_color(
    camera: str,
    business_day: date | str,
    color: str,
    bags: int,
    reason: str,
    user=None,
) -> dict:
    """Give bags the camera and the resolver left without a colour one colour.

    The oldest pending bags of the shift get ``color_resolution="manual"``;
    their original camera answer stays untouched. For an open shift that is
    all: the 19:00 posting counts them under the colour. A shift already
    posted receives them now as a separate ``manual_color`` receipt, so the
    immutable 19:00 posting is never rewritten. Every call is audited.
    ``bags`` is the positive count validated by AlwaysOnUnknownColorSerializer.
    """

    camera = assert_contour_camera(camera, ANALYTICS_SCOPE_AI247)
    day = _selected_day(business_day)
    if day is None:
        raise ValidationError({"business_day": "Укажите смену"})
    color = _normalize_color(color)
    if color in color_resolution.PENDING_COLORS or color == "unclassified":
        raise ValidationError({"color": "Выберите цвет продукции"})
    reason = " ".join(str(reason or "").split())
    if len(reason) < 5 or len(reason) > 500:
        raise ValidationError({"reason": "Укажите причину от 5 до 500 символов"})
    now = timezone.now()
    if day > business_day_for(now):
        raise ValidationError({"business_day": "Эта смена ещё не началась"})

    # Same lock order as event ingestion and _post_one: cursor → route →
    # batch → catalogue → stock.
    AlwaysOnCounterCursor.locked(camera)
    _warehouse_for_camera(camera, lock=True, require_active=False)
    batch = (
        AlwaysOnStockBatch.objects.select_for_update()
        .filter(camera=camera, business_day=day)
        .first()
    )
    posted = batch is not None and batch.status in TERMINAL_BATCH_STATUSES
    if posted:
        pending = batch.pending_bags
    else:
        color_resolution.resolve_business_day(camera, day)
        pending = sum(
            max(0, _day_totals(camera, day).get(key, {}).get("net_bags", 0))
            for key in color_resolution.PENDING_COLORS
        )
    if bags > pending:
        raise ValidationError({"bags": f"Без цвета осталось только {pending} меш."})

    # Locks the joined product row too, as in _post_one.
    mapping = (
        AlwaysOnColorProductMapping.objects.select_for_update()
        .filter(camera=camera, color=color)
        .select_related("product")
        .first()
    )
    if mapping is None or not mapping.product.is_active:
        raise ValidationError(
            {
                "color": (
                    f"Для цвета «{color_label(color)}» не выбран товар "
                    "в разделе «Куда приходовать»"
                )
            }
        )

    events = color_resolution.pending_unknown_events(camera, day, bags)
    if len(events) < bags:
        raise ValidationError({"bags": "Мешки без цвета не найдены в журнале камеры"})
    actor = getattr(user, "username", "") or "система"
    note = f"вручную ({actor}): {reason}"[:300]
    for event in events:
        event.resolved_color = color
        event.color_resolution = METHOD_MANUAL
        event.resolution_note = {**(event.resolution_note or {}), "color": note}
    AlwaysOnImportedEvent.objects.bulk_update(
        events, ["resolved_color", "color_resolution", "resolution_note"]
    )

    receipt = None
    if batch is not None and not posted and batch.pending_bags:
        # A blocked shift shows what is still without a colour; the bags now
        # join its posting, so the count shown next to the error drops too.
        batch.pending_bags = max(0, pending - bags)
        batch.save(update_fields=["pending_bags", "updated_at"])
    if posted:
        warehouse = batch.warehouse
        product = mapping.product
        receipt = receive_stock(
            product,
            bags,
            user=user,
            note=(
                f"AI 24/7 · {camera} · смена {day.isoformat()} · "
                f"цвет {color} указан вручную"
            ),
            warehouse=warehouse,
            require_active=False,
        )
        AlwaysOnStockPosting.objects.create(
            batch=batch,
            kind=AlwaysOnStockPosting.MANUAL_COLOR,
            color=color,
            product=product,
            detected_bags=0,
            resolved_bags=bags,
            correction_bags=0,
            posted_bags=bags,
            receipt=receipt,
            created_by=user if getattr(user, "pk", None) else None,
        )
        batch.total_bags += bags
        batch.pending_bags -= bags
        if batch.status == AlwaysOnStockBatch.EMPTY:
            batch.status = AlwaysOnStockBatch.POSTED
        batch.posted_at = batch.posted_at or now
        batch.save(
            update_fields=[
                "status",
                "total_bags",
                "pending_bags",
                "posted_at",
                "updated_at",
            ]
        )

    log_event(
        "always_on_unknown_color_assigned",
        (
            f"AI 24/7 · {camera}: {bags} меш. без цвета за смену "
            f"{day:%d.%m.%Y} — «{color_label(color)}»"
            + (" (оприходовано)" if posted else "")
            + f". Причина: {reason}"
        ),
        user=user if getattr(user, "pk", None) else None,
        payload={
            "camera": camera,
            "business_day": day.isoformat(),
            "color": color,
            "product": mapping.product_id,
            "bags": bags,
            "events": [event.upstream_event_id for event in events],
            "batch": batch.pk if batch else None,
            "receipt": receipt.pk if receipt else None,
            "posted": posted,
            "reason": reason,
        },
    )
    return production_payload(camera)
