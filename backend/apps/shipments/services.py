from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.eventlog.services import log_event
from apps.warehouse.services import deduct_stock, resolve_warehouse

from .models import Shipment

LOADING_CAMERA_CONSTRAINT = "orders_one_active_order_per_loading_camera"


def _is_loading_camera_conflict(exc: IntegrityError) -> bool:
    cause = exc.__cause__
    diagnostic = getattr(cause, "diag", None)
    return getattr(diagnostic, "constraint_name", None) == LOADING_CAMERA_CONSTRAINT


def _camera_busy_error(exc: IntegrityError) -> ValidationError:
    return ValidationError({
        "detail": "Камера уже закреплена за другим активным заказом",
        "code": "camera_busy",
    })


def _locked(order, user=None):
    """Перечитать заказ под блокировкой строки. Переходы статуса — это
    read-check-write: без блокировки двойной клик или две вкладки провели бы
    один шаг дважды (у отгрузки — двойное списание склада и двойной долг)."""
    from apps.orders.services import lock_live_order

    return lock_live_order(order, user)


def _device_for(user):
    return getattr(user, "active_monoblock_device", None)


def _assert_device_order_camera(order, user) -> None:
    """Keep physical monoblocks inside the workflow for their own camera."""
    device = _device_for(user)
    if device is not None and order.loading_camera != device.camera_source:
        raise PermissionDenied("Эта отгрузка закреплена за другим моноблоком")


def assert_device_camera_change(order, camera: str, user) -> None:
    """A device may bind/release only its camera and never another binding."""
    device = _device_for(user)
    if device is None:
        return
    if order.loading_camera and order.loading_camera != device.camera_source:
        raise PermissionDenied("Эта отгрузка закреплена за другим моноблоком")
    if camera and camera != device.camera_source:
        raise PermissionDenied("Эта камера закреплена за другим моноблоком")


def _validate_loading_camera_available(order, camera: str) -> None:
    if not camera:
        return
    conflict = (
        # Do not lock the conflicting Order here: two different camera starts
        # may each already own their own parent/session and attempt a swap,
        # which would create an O_A -> O_B / O_B -> O_A deadlock. Manual
        # changes use the shared camera mutex; AI starts are session-serialized,
        # and the named partial UNIQUE constraint remains the final arbiter.
        type(order).objects
        .filter(
            loading_camera=camera,
            status__in=("confirmed", "arrived", "loading"),
            deleted_at__isnull=True,
        )
        .exclude(pk=order.pk)
        .only("id")
        .first()
    )
    if conflict:
        raise ValidationError({
            "detail": f"Камера уже закреплена за заказом #{conflict.pk}",
            "code": "camera_busy",
            "order_id": conflict.pk,
        })


@transaction.atomic
def _set_loading_camera_locked(order, camera: str, user=None):
    # Share the same camera-ownership mutex with AI start and device binding.
    # Taking it before the Order row also prevents A -> B / B -> A camera
    # swaps from acquiring conflicting order locks in opposite directions.
    from apps.cameras.sessions import lock_camera_binding

    lock_camera_binding()
    order = _locked(order, user)
    assert_device_camera_change(order, camera, user)
    if camera and order.status not in ("arrived", "loading"):
        raise ValidationError({
            "detail": "Камеру можно закрепить только после въезда и до завершения погрузки",
            "code": "invalid_status",
        })
    if camera != order.loading_camera:
        _assert_no_open_ai_session(order)
    if camera:
        from apps.cameras.models import AiCountingSession

        if AiCountingSession.objects.filter(
            camera=camera,
            status__in=AiCountingSession.OPEN_STATUSES,
        ).exclude(order_id=order.pk).exists():
            raise ValidationError({
                "detail": "Камера уже зарезервирована AI-погрузкой",
                "code": "camera_busy",
            })
    _validate_loading_camera_available(order, camera)
    order.loading_camera = camera
    order.save(update_fields=["loading_camera"])
    return order


def set_loading_camera(order, camera: str, user=None):
    """Assign or release an order camera while serializing changes to the order."""
    try:
        return _set_loading_camera_locked(order, camera, user)
    except IntegrityError as exc:
        if not _is_loading_camera_conflict(exc):
            raise
        raise _camera_busy_error(exc) from exc


def _require_shipment(order):
    shipment = getattr(order, "shipment", None)
    if shipment is None:
        raise ValidationError(
            {"detail": "Для заказа нет записи загрузки",
             "code": "shipment_required"}
        )
    return shipment


def _require_transport(order, kind):
    if order.transport_type != kind:
        raise ValidationError(
            {"detail": "Этот шаг недоступен для выбранного вида транспорта",
             "code": "wrong_transport"}
        )


def estimated_load_kg(order) -> Decimal:
    """Расчётный вес груза по мешкам: Σ(кол-во × вес фасовки)."""
    return sum(
        (i.quantity * i.product_weight_kg for i in order.items.all()), Decimal(0)
    )


def _lock_stock_rows(
    items,
    warehouse=None,
    *,
    require_active=True,
) -> None:
    """Acquire stock locks in one global product order.

    Without a deterministic order, two mixed-product shipments containing
    A/B and B/A can deadlock while each waits for the other's StockItem.
    Creating a zero row here also makes the existing allow-negative behavior
    deterministic when a product has no stock row yet.
    """
    from apps.warehouse.services import lock_stock_item

    products = {item.product_id: item.product for item in items}
    for product_id in sorted(products):
        lock_stock_item(
            products[product_id],
            warehouse=warehouse,
            require_active=require_active,
        )


@transaction.atomic
def begin_camera_loading(
    order,
    camera: str,
    user,
):
    """Закрепить свободную камеру и перевести заказ в активную погрузку.

    Моноблок вызывает эту операцию перед запуском модели. Поэтому заказ из
    `confirmed` покидает очередь готовых к погрузке в момент фактического старта
    с выбранной камерой. Одна камера может принадлежать только одному живому
    заказу; ограничение продублировано частичным UNIQUE-индексом в PostgreSQL.
    """
    order = _locked(order, user)
    restoring_same_binding = (
        order.status == "loading" and order.loading_camera == camera
    )
    if order.status not in ("confirmed", "arrived") and not restoring_same_binding:
        raise ValidationError({
            "detail": "Загрузку можно начать только для подтверждённого или прибывшего заказа",
            "code": "invalid_status",
        })

    assert_device_camera_change(order, camera, user)
    _validate_loading_camera_available(order, camera)

    now = timezone.now()
    old_status = order.status
    shipment, _ = Shipment.objects.select_for_update().get_or_create(order=order)
    shipment.loading_started_at = shipment.loading_started_at or now
    shipment.save(update_fields=["loading_started_at"])

    order.status = "loading"
    order.loading_camera = camera
    try:
        # Isolate the constraint failure in a savepoint so it can be mapped to
        # ValidationError. counting.start then runs its normal compensation
        # path instead of leaking a 500 and an orphan STARTING reservation.
        with transaction.atomic():
            order.save(update_fields=["status", "loading_camera"])
    except IntegrityError as exc:
        if not _is_loading_camera_conflict(exc):
            raise
        raise _camera_busy_error(exc) from exc
    if old_status != "loading":
        log_event(
            "loading_start",
            "Начата загрузка через Моноблок",
            user=user,
            order=order,
            payload={"camera": camera, "from": old_status},
        )
    log_event(
        "camera_bound",
        f"Камера {camera} закреплена за заказом",
        user=user,
        order=order,
        payload={"camera": camera},
    )
    return order


@transaction.atomic
def record_arrival(order, weigh_in_kg, user):
    """Зафиксировать прибытие машины и её входной вес."""
    order = _locked(order, user)
    _require_transport(order, "truck")
    if order.status != "confirmed":
        raise ValidationError(
            {"detail": "Машину можно принять только для подтверждённого заказа",
             "code": "invalid_status"}
        )
    if weigh_in_kg is None:
        weigh_in_kg = estimated_load_kg(order)
        weight_source = "estimated"
    else:
        weight_source = "manual"
    truck = order.truck_number
    order.status = "arrived"
    order.save(update_fields=["status"])
    shipment, _ = Shipment.objects.get_or_create(
        order=order, defaults={"truck_number": truck}
    )
    shipment.truck_number = truck
    shipment.weigh_in_kg = weigh_in_kg
    shipment.arrived_at = timezone.now()
    shipment.save()
    log_event("arrival", f"Машина {truck} прибыла", user=user, order=order,
              payload={
                  "weigh_in_kg": str(weigh_in_kg),
                  "source": weight_source,
              })
    return shipment


@transaction.atomic
def record_count(order, bags, user):
    order = _locked(order, user)
    _assert_device_order_camera(order, user)
    if order.status in ("arrived", "loading"):
        shipment = _require_shipment(order)
    else:
        raise ValidationError(
            {"detail": "Подсчёт мешков возможен только во время загрузки",
             "code": "invalid_status"}
        )

    if order.status == "arrived":
        order.status = "loading"
        order.save(update_fields=["status"])
        log_event("loading_start", "Начата загрузка", user=user, order=order)
    shipment.bags_loaded = bags
    shipment.save(update_fields=["bags_loaded"])
    log_event("loading", f"Посчитано {bags} мешков", user=user, order=order,
              payload={"bags": bags})
    return shipment


def _assert_no_open_ai_session(order) -> None:
    """Manual completion must not bypass an open AI counting session."""
    # Local import avoids a shipments -> cameras -> shipments import cycle.
    from apps.cameras.models import AiCountingSession

    if AiCountingSession.objects.filter(
        order_id=order.pk,
        status__in=AiCountingSession.OPEN_STATUSES,
    ).exists():
        raise ValidationError({
            "detail": "Сначала завершите AI-подсчёт на Моноблоке",
            "code": "ai_session_active",
        })


@transaction.atomic
def finish_loading(order, user):
    order = _locked(order, user)
    _assert_device_order_camera(order, user)
    _require_transport(order, "truck")
    if order.status != "loading":
        raise ValidationError(
            {"detail": "Завершить можно только идущую загрузку", "code": "invalid_status"}
        )
    _assert_no_open_ai_session(order)
    shipment = _require_shipment(order)
    log_event("loading_done", "Загрузка завершена", user=user, order=order,
              payload={"bags": shipment.bags_loaded})
    order.status = "loaded"
    order.loading_camera = ""
    order.save(update_fields=["status", "loading_camera"])
    return shipment


def _valid_ai_total(bags) -> bool:
    """Годное число мешков от воркера: целое неотрицательное, но не bool."""
    return not isinstance(bags, bool) and isinstance(bags, int) and bags >= 0


@transaction.atomic
def finish_ai_counting(order, bags: int, user):
    """Сохранить финальный AI-счёт и завершить загрузку.

    Воркер на ПК цеха — сторонний процесс, и его ответ может прийти пустым
    или битым. Раньше это роняло завершение посреди разбора AI-сессии: заказ
    оставался в ``loading`` с открытой сессией, а её наличие блокировало и
    ручное завершение, и откат.
    Поэтому негодное число не блокирует завершение подсчёта: за факт берётся
    заказанное количество, а расхождение попадает в журнал.
    """
    order = _locked(order, user)
    _assert_device_order_camera(order, user)
    if order.status != "loading":
        raise ValidationError({
            "detail": "Завершить можно только идущую загрузку",
            "code": "invalid_status",
        })
    shipment = _require_shipment(order)

    source = "ai_final"
    if not _valid_ai_total(bags):
        rejected, bags = bags, sum(item.quantity for item in order.items.all())
        source = "ai_final_fallback"
        log_event(
            "loading",
            f"AI-сервис вернул некорректный счёт — принято по заказу: {bags} мешков",
            user=user,
            order=order,
            payload={"bags": bags, "source": source,
                     "rejected_total": repr(rejected)},
        )

    shipment.bags_loaded = bags
    shipment.save(update_fields=["bags_loaded"])
    log_event(
        "loading",
        f"AI-подсчёт зафиксирован: {bags} мешков",
        user=user,
        order=order,
        payload={"bags": bags, "source": source},
    )
    log_event(
        "loading_done",
        "Загрузка завершена по финальному AI-подсчёту",
        user=user,
        order=order,
        payload={"bags": bags, "source": source},
    )
    order.status = "loaded"
    order.loading_camera = ""
    order.save(update_fields=["status", "loading_camera"])
    return shipment


@transaction.atomic
def manual_complete_order(order, bags: int | None, user):
    """Завершить подтверждённый заказ без привязки к камере.

    Это административный путь для борда и списка заказов. В отличие от голой
    смены ``status`` он создаёт полноценную Shipment, фиксирует количество,
    списывает склад и освобождает возможную старую привязку камеры. Отсутствие
    ``bags`` означает «без ручного подсчёта»: используем количество из заказа.
    Работающую AI-сессию намеренно не обрываем из этого endpoint — сначала её
    должен остановить владелец или администратор на посту.
    """
    order = _locked(order, user)
    if order.status not in ("confirmed", "arrived", "loading", "loaded"):
        raise ValidationError({
            "detail": "Вручную завершить можно только подтверждённый или загружаемый заказ",
            "code": "invalid_status",
        })

    _assert_no_open_ai_session(order)

    existing_shipment = Shipment.objects.filter(order=order).first()
    if bags is None:
        if existing_shipment is not None and order.status in ("arrived", "loading", "loaded"):
            bags = existing_shipment.bags_loaded
            count_source = "current"
        else:
            bags = sum(item.quantity for item in order.items.all())
            count_source = "ordered"
    else:
        if isinstance(bags, bool) or not isinstance(bags, int) or bags < 0:
            raise ValidationError({
                "detail": "Количество мешков должно быть целым числом от 0",
                "code": "invalid_bags",
            })
        count_source = "manual"

    now = timezone.now()
    shipment = existing_shipment
    if shipment is None:
        shipment = Shipment.objects.create(
            order=order,
            truck_number=order.truck_number if order.transport_type == "truck" else "",
        )
    if order.transport_type == "truck":
        shipment.truck_number = order.truck_number
        if shipment.weigh_in_kg is None:
            shipment.weigh_in_kg = estimated_load_kg(order)
        shipment.arrived_at = shipment.arrived_at or now
    shipment.loading_started_at = shipment.loading_started_at or now
    shipment.bags_loaded = bags
    shipment.save()
    log_event(
        "loading_done",
        f"Отгрузка завершена вручную: {bags} мешков",
        user=user,
        order=order,
        payload={"bags": bags, "source": "manual_override", "count_source": count_source},
    )
    label = (
        "Вагон: отгрузка завершена вручную"
        if order.transport_type == "train"
        else f"Машина {shipment.truck_number}: отгрузка завершена вручную"
    )
    return _do_ship(order, shipment, user, label)


@transaction.atomic
def rewind_loading(order, user, target_status="confirmed"):
    """Вернуть въехавший/загружаемый заказ обратно в ожидание въезда.

    Это отдельная бизнес-операция, а не голая ручная смена статуса: очищаем
    незавершённую отгрузку и освобождаем назначенную камеру. Работающую
    AI-сессию сначала обязан остановить её автор или администратор.
    """
    order = _locked(order, user)
    _assert_device_order_camera(order, user)
    if target_status not in ("pending", "confirmed", "cancelled"):
        raise ValidationError({
            "detail": "Недопустимый целевой статус возврата",
            "code": "bad_status",
        })
    if order.status not in ("arrived", "loading", "loaded"):
        raise ValidationError({
            "detail": "Вернуть можно только незавершённую отгрузку",
            "code": "invalid_status",
        })

    # Импорт локальный: cameras зависит от orders, а доменная операция не
    # должна создавать циклический импорт при старте Django.
    from apps.cameras.models import AiCountingSession
    has_open_ai = AiCountingSession.objects.filter(
        order=order,
        status__in=AiCountingSession.OPEN_STATUSES,
    ).exists()
    if has_open_ai:
        raise ValidationError({
            "detail": "Сначала остановите AI-подсчёт. Это может сделать начавший отгрузку или администратор",
            "code": "ai_session_active",
        })

    old = order.status
    shipment = getattr(order, "shipment", None)
    reset_bags = shipment.bags_loaded if shipment else 0
    if shipment:
        shipment.delete()
    order.status = target_status
    order.loading_camera = ""
    order.save(update_fields=["status", "loading_camera"])
    target_labels = {
        "pending": "на рассмотрение",
        "confirmed": "в ожидание въезда",
        "cancelled": "в отменённые",
    }
    log_event(
        "shipping_rewind",
        f"Незавершённая отгрузка сброшена; заказ переведён {target_labels[target_status]}",
        user=user,
        order=order,
        payload={"from": old, "to": target_status, "reset_bags": reset_bags},
    )
    return order


@transaction.atomic
def rollback_shipment(order, user, *, target_status: str, reason: str):
    """Controlled reversal of a completed shipment.

    The operation is deliberately separate from generic status editing: it
    restores stock, clears shipment state, removes local camera recordings and
    writes an immutable audit entry with the author and required reason.
    """
    order = _locked(order, user)
    if target_status not in ("pending", "confirmed", "cancelled"):
        raise ValidationError({
            "detail": "Вернуть отгруженный заказ можно на рассмотрение, в ожидание или в отменённые",
            "code": "bad_status",
        })
    reason = " ".join(str(reason or "").split())
    if len(reason) < 5:
        raise ValidationError({
            "detail": "Укажите причину отката (минимум 5 символов)",
            "code": "rollback_reason_required",
        })
    if len(reason) > 500:
        raise ValidationError({"detail": "Причина слишком длинная", "code": "reason_too_long"})
    if order.status != "shipped":
        raise ValidationError({
            "detail": "Откат доступен только для отгруженного заказа",
            "code": "invalid_status",
        })
    if order.payments.exclude(status="rejected").exists():
        raise ValidationError({
            "detail": "Сначала отмените или откройте все оплаты по заказу",
            "code": "payments_exist",
        })

    items = list(
        order.items.select_related("product").order_by("product_id", "id")
    )
    deleted_products = [item.product_label for item in items if item.product_id is None]
    if deleted_products:
        raise ValidationError({
            "detail": "Нельзя восстановить склад: удалены товары — " + ", ".join(deleted_products),
            "code": "product_deleted",
        })

    # The warehouse was selected while the order was still editable. It may be
    # deactivated later, but historical fulfillment and rollback must continue
    # against that immutable pin. Lock stock before deleting external video so
    # a warehouse-domain error cannot leave media deleted while the DB rolls
    # back unchanged.
    warehouse = resolve_warehouse(order.warehouse, require_active=False)
    _lock_stock_rows(items, warehouse, require_active=False)
    if order.warehouse_id is None:
        order.warehouse = warehouse
        order.save(update_fields=["warehouse"])

    # Удаление локального видео — сопутствующая очистка, а не часть складской
    # транзакции. Недоступный ПК камер не должен блокировать контролируемый
    # откат: запись всё равно исчезнет по локальной политике хранения, а сбой
    # очистки сохраняется в журнале для администратора.
    from apps.cameras import recordings
    from apps.cameras.models import AiCountingSession
    sessions = list(AiCountingSession.objects.select_for_update().filter(order=order))
    shipment = Shipment.objects.select_for_update().filter(order=order).first()
    deleted_segments = 0
    cleaned_session_ids = []
    cleanup_pending_session_ids = []
    for session in sessions:
        if not session.recording_stream:
            continue
        end = session.ended_at or (shipment.shipped_at if shipment else None) or timezone.now()
        try:
            deleted_segments += recordings.delete_session_segments(
                session.recording_stream, session.started_at, end)
            cleaned_session_ids.append(session.pk)
        except recordings.RecordingUnavailable:
            cleanup_pending_session_ids.append(session.pk)

    from apps.warehouse.services import adjust_stock

    restored = 0
    for item in items:
        adjust_stock(
            item.product,
            item.quantity,
            user,
            note=f"Откат отгрузки заказа #{order.pk}: {reason}",
            warehouse=warehouse,
            require_active=False,
        )
        restored += item.quantity

    previous_bags = shipment.bags_loaded if shipment else 0
    if shipment:
        shipment.delete()
    AiCountingSession.objects.filter(pk__in=cleaned_session_ids).update(
        recording_stream="",
        error="Видео удалено при откате отгрузки",
    )
    AiCountingSession.objects.filter(pk__in=cleanup_pending_session_ids).update(
        error=(
            "Отгрузка отменена; видео не удалось удалить сразу. "
            "Оно будет удалено по локальному сроку хранения"
        ),
    )
    order.status = target_status
    order.payment_status = "unpaid"
    order.loading_camera = ""
    order.save(update_fields=["status", "payment_status", "loading_camera"])
    log_event(
        "shipment_rollback",
        f"Отгрузка заказа #{order.pk} отменена. Причина: {reason}",
        user=user,
        order=order,
        payload={
            "from": "shipped", "to": target_status, "reason": reason,
            "restored_bags": restored, "previous_bags_loaded": previous_bags,
            "recording_segments_deleted": deleted_segments,
            "recording_session_ids": [session.pk for session in sessions],
            "recording_cleanup_pending_session_ids": cleanup_pending_session_ids,
        },
    )
    return order


def _do_ship(order, shipment, user, label):
    """Списать со склада и зафиксировать отгрузку. Общее для трака и вагона."""
    items = list(
        order.items.select_related("product").order_by("product_id", "id")
    )
    for item in items:
        if item.product_id is None:
            raise ValidationError({
                "detail": f"Товар «{item.product_label}» удалён. Обновите состав заказа.",
                "code": "product_deleted",
            })
    warehouse = resolve_warehouse(order.warehouse, require_active=False)
    _lock_stock_rows(items, warehouse, require_active=False)
    if order.warehouse_id is None:
        order.warehouse = warehouse
        order.save(update_fields=["warehouse"])
    for item in items:
        deduct_stock(
            item.product,
            item.quantity,
            user,
            allow_negative=True,
            warehouse=warehouse,
            require_active=False,
        )
    shipment.shipped_at = timezone.now()
    shipment.save()
    order.status = "shipped"
    order.payment_status = "unpaid"
    order.loading_camera = ""
    order.save(update_fields=["status", "payment_status", "loading_camera"])
    if order.settlement_intent == "debt":
        log_event(
            "debt",
            f"Заказ отгружен в долг: {order.total_amount}",
            user=user,
            order=order,
            payload={
                "amount": str(order.total_amount),
                "intent": order.settlement_intent,
            },
        )
    bag_estimate = estimated_load_kg(order)
    log_event("shipment", label, user=user, order=order,
              payload={"bags_loaded": shipment.bags_loaded,
                       "bag_estimate_kg": str(bag_estimate),
                       "amount": str(order.total_amount),
                       "settlement_intent": order.settlement_intent,
                       "weigh_in_kg": (
                           str(shipment.weigh_in_kg)
                           if shipment.weigh_in_kg is not None else None
                       )})
    return shipment


@transaction.atomic
def record_shipment(order, user):
    order = _locked(order, user)
    if order.status != "loaded":
        raise ValidationError(
            {"detail": "Выезд возможен только после завершения загрузки",
             "code": "invalid_status"}
        )
    _assert_no_open_ai_session(order)
    shipment = _require_shipment(order)
    if order.transport_type == "truck":
        shipment.truck_number = order.truck_number
    label = (
        "Вагон отгружен"
        if order.transport_type == "train"
        else f"Машина {order.truck_number} выехала"
    )
    return _do_ship(order, shipment, user, label)


@transaction.atomic
def start_train_loading(order, user):
    """Вагон: старт сессии загрузки (без въезда и взвешивания)."""
    order = _locked(order, user)
    _require_transport(order, "train")
    if order.status != "confirmed":
        raise ValidationError(
            {"detail": "Загрузку вагона можно начать только для подтверждённого заказа",
             "code": "invalid_status"}
        )
    shipment, _ = Shipment.objects.get_or_create(order=order)
    shipment.loading_started_at = timezone.now()
    shipment.save()
    order.status = "loading"
    order.save(update_fields=["status"])
    log_event("loading_start", "Вагон: начата загрузка", user=user, order=order)
    return shipment


@transaction.atomic
def finish_train_loading(order, user):
    """Вагон: завершить загрузку и подготовить к отгрузке."""
    order = _locked(order, user)
    _require_transport(order, "train")
    if order.status != "loading":
        raise ValidationError(
            {"detail": "Завершить можно только идущую загрузку вагона",
             "code": "invalid_status"}
        )
    _assert_no_open_ai_session(order)
    shipment = _require_shipment(order)
    log_event("loading_done", "Вагон: загрузка завершена", user=user, order=order,
              payload={"bags": shipment.bags_loaded})
    order.status = "loaded"
    order.loading_camera = ""
    order.save(update_fields=["status", "loading_camera"])
    return shipment
