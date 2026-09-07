"""Commands for production configuration, corrections and atomic stock posting.

Public entry points stay here for existing API/worker callers. Read projections,
warehouse rules and the run ledger live in their respective focused modules.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.catalog.models import Product
from apps.eventlog.services import log_event
from apps.warehouse.models import StockItem, Warehouse
from apps.warehouse.services import lock_stock_item, receive_stock

from . import ai
from .models import (
    ANALYTICS_SCOPE_AI247,
    AlwaysOnColorProductMapping,
    AlwaysOnCounterCursor,
    AlwaysOnProductionCorrection,
    AlwaysOnProductionRun,
    AlwaysOnStockBatch,
    AlwaysOnStockPosting,
    AlwaysOnWarehouseRoute,
    ContinuousCameraRole,
)
from .production_catalog import (
    BASE_COLORS,
    COLOR_LABELS,
    _compatibility_warehouse,
    _stock_scope_for_warehouse,
    _warehouse_for_camera,
)
from .production_queries import (
    _batch_payload,
    _run_color_totals,
    production_payload,
    smooth_day_runs,
)
from .production_runs import (
    RUN_GAP,
    _aware,
    _day_totals,
    _normalize_color,
    business_day_for,
    close_stale_runs,
    record_color_deltas,
    scheduled_for,
)

log = logging.getLogger(__name__)
# Keep a clean post-cutoff journal observation before making stock immutable.
EVENT_SETTLE_DELAY = timedelta(minutes=1)
TERMINAL_BATCH_STATUSES = frozenset(
    {AlwaysOnStockBatch.POSTED, AlwaysOnStockBatch.EMPTY}
)

__all__ = [
    "RUN_GAP",
    "TERMINAL_BATCH_STATUSES",
    "business_day_for",
    "scheduled_for",
    "record_color_deltas",
    "close_stale_runs",
    "production_payload",
    "smooth_day_runs",
    "save_mappings",
    "record_correction",
    "post_due_stock",
    "retry_batch",
    "_run_color_totals",
]


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
    warehouse=None,
) -> dict:
    camera = ai.normalize(camera)
    if not isinstance(mappings, list):
        raise ValidationError({"mappings": "Передайте список привязок"})

    # Event ingestion and stock posting take this per-camera mutex first.
    # Taking the same lock before reading runs/batches closes the window where
    # a page from the old route is in flight but not committed yet.
    AlwaysOnCounterCursor.objects.select_for_update().get_or_create(camera=camera)
    # Warehouse configuration and transfers always lock the stable main row
    # first. Follow the same order here, then lock current/selected ids in
    # ascending order; a secondary -> main route change must not invert it.
    compatibility_warehouse = _compatibility_warehouse(lock=True)
    route = (
        AlwaysOnWarehouseRoute.objects.select_for_update().filter(camera=camera).first()
    )
    current_warehouse_id = (
        route.warehouse_id
        if route and route.warehouse_id
        else compatibility_warehouse.pk
    )
    if warehouse is None:
        selected_warehouse_id = current_warehouse_id
    elif isinstance(warehouse, Warehouse):
        selected_warehouse_id = warehouse.pk
    elif isinstance(warehouse, bool):
        selected_warehouse_id = 0
    else:
        try:
            selected_warehouse_id = int(warehouse)
        except (TypeError, ValueError):
            selected_warehouse_id = 0

    locked_warehouses = {compatibility_warehouse.pk: compatibility_warehouse}
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
    products = (
        Product.objects.select_for_update()
        .filter(pk__in=product_ids)
        .order_by("pk")
        .in_bulk()
    )
    stock_by_product = {
        row.product_id: row
        for row in StockItem.objects.select_for_update(of=("self",))
        .filter(
            _stock_scope_for_warehouse(selected_warehouse),
            product_id__in=product_ids,
        )
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
            expected = COLOR_LABELS[color]
            raise ValidationError(
                {
                    "mappings": f"Для цвета «{expected}» выберите товар того же цвета",
                }
            )
    # Persist a stock card for this exact route. The same catalogue product may
    # be produced and held in several warehouses independently.
    for product_id in sorted(product_ids):
        if product_id not in stock_by_product:
            try:
                stock_by_product[product_id] = lock_stock_item(
                    products[product_id],
                    selected_warehouse,
                    require_active=False,
                )
            except ValidationError as exc:
                if exc.detail.get("code") != "product_in_other_warehouse":
                    raise
                raise ValidationError(
                    {
                        "mappings": str(exc.detail["detail"]),
                        "code": "product_assigned_other_warehouse",
                    }
                ) from exc

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
    try:
        batch = AlwaysOnStockBatch.objects.select_for_update().get(
            camera=camera,
            business_day=business_day,
        )
        if batch.warehouse_id is None:
            batch.warehouse = warehouse
            batch.save(update_fields=["warehouse", "updated_at"])
        return batch
    except AlwaysOnStockBatch.DoesNotExist:
        pass
    try:
        with transaction.atomic():
            return AlwaysOnStockBatch.objects.create(
                camera=camera,
                warehouse=warehouse,
                business_day=business_day,
                scheduled_for=scheduled_for(business_day),
            )
    except IntegrityError:
        batch = AlwaysOnStockBatch.objects.select_for_update().get(
            camera=camera,
            business_day=business_day,
        )
        if batch.warehouse_id is None:
            batch.warehouse = warehouse
            batch.save(update_fields=["warehouse", "updated_at"])
        return batch


def _assert_ai247_role(camera: str) -> None:
    if not ContinuousCameraRole.objects.filter(
        camera=camera,
        analytics_scope=ANALYTICS_SCOPE_AI247,
    ).exists():
        raise ValidationError(
            {
                "detail": "Камера не закреплена за контуром AI 24/7",
                "code": "camera_not_in_ai247",
            }
        )


@transaction.atomic
def _post_one(camera: str, business_day: date, now: datetime) -> AlwaysOnStockBatch:
    _assert_ai247_role(camera)
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
                "warehouse": batch.warehouse_id,
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

    # Lock the exact catalogue rows whose active state was just validated.
    product_ids = {mapping_by_color[color].product_id for color in positive}
    locked_products = (
        Product.objects.select_for_update()
        .filter(pk__in=product_ids)
        .order_by("pk")
        .in_bulk()
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
            warehouse=batch.warehouse,
            require_active=False,
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
            "warehouse": batch.warehouse_id,
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


def post_due_stock(now: datetime | None = None) -> list[dict]:
    """Post every overdue camera/day independently and retry safe failures."""

    now = _aware(now)
    close_stale_runs(now, reserved_ai247_only=True)
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
    _assert_ai247_role(batch.camera)
    if batch.status in TERMINAL_BATCH_STATUSES:
        return _batch_payload(batch)
    now = timezone.now()
    try:
        batch = _post_one(batch.camera, batch.business_day, now)
    except Exception as exc:
        log.exception("AI 24/7 manual stock retry failed batch=%s", batch_id)
        batch = _mark_failed(batch.camera, batch.business_day, exc)
    return _batch_payload(batch)
