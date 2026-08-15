from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.eventlog.services import log_event
from apps.warehouse.services import deduct_stock

from . import scale
from .models import Shipment

LOADING_CAMERA_CONSTRAINT = "orders_one_active_order_per_loading_camera"


@dataclass(frozen=True)
class BoundScaleReading:
    """A scale sample bound to the truck number observed before network I/O."""

    truck_number: str
    reading: scale.ScaleReading


def _locked(order):
    """Перечитать заказ под блокировкой строки. Переходы статуса — это
    read-check-write: без блокировки двойной клик или две вкладки провели бы
    один шаг дважды (у отгрузки — двойное списание склада и двойной долг)."""
    return type(order).objects.select_for_update().get(pk=order.pk)


def _device_for(user):
    return getattr(user, "active_monoblock_device", None)


def _assert_device_order_camera(order, user) -> None:
    """Keep physical monoblocks inside the workflow for their own camera."""
    device = _device_for(user)
    if device is not None and order.loading_camera != device.camera_source:
        raise PermissionDenied("Эта отгрузка закреплена за другим моноблоком")


def _assert_device_camera_change(order, camera: str, user) -> None:
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
        type(order).objects.select_for_update()
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
    order = _locked(order)
    _assert_device_camera_change(order, camera, user)
    _validate_loading_camera_available(order, camera)
    order.loading_camera = camera
    order.save(update_fields=["loading_camera"])
    return order


def set_loading_camera(order, camera: str, user=None):
    """Assign or release an order camera while serializing changes to the order."""
    try:
        return _set_loading_camera_locked(order, camera, user)
    except IntegrityError as exc:
        cause = exc.__cause__
        diagnostic = getattr(cause, "diag", None)
        if getattr(diagnostic, "constraint_name", None) != LOADING_CAMERA_CONSTRAINT:
            raise
        raise ValidationError({
            "detail": "Камера уже закреплена за другим активным заказом",
            "code": "camera_busy",
        }) from exc


def _require_shipment(order):
    shipment = getattr(order, "shipment", None)
    if shipment is None:
        raise ValidationError(
            {"detail": "Сначала нужно принять машину: для заказа нет отгрузки",
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
        (i.quantity * i.product_weight_kg for i in order.items.all()), Decimal("0")
    )


def _matching_scale_entry(
    shipment: Shipment | None,
    truck_number: str,
    *,
    reservation_id: int | None = None,
    camera: str | None = None,
) -> bool:
    if (
        shipment is None
        or shipment.truck_number != truck_number
        or shipment.weigh_in_source != Shipment.WeightSource.SCALE
        or shipment.weigh_in_kg is None
    ):
        return False
    if truck_number.strip():
        return True
    return bool(
        reservation_id is not None
        and camera
        and shipment.weigh_in_session_id == reservation_id
        and shipment.weigh_in_camera == camera
    )


def _lock_start_reservation(
    reservation_id: int | None,
    order_id: int,
    camera: str | None,
) -> None:
    if reservation_id is None and camera is None:
        return
    if reservation_id is None or not camera:
        raise ValueError("reservation_id and camera must be provided together")

    # Local import avoids shipments -> cameras -> shipments at Django startup.
    from apps.cameras.models import AiCountingSession

    reservation_exists = AiCountingSession.objects.select_for_update().filter(
        pk=reservation_id,
        order_id=order_id,
        camera=camera,
        status__in=AiCountingSession.OPEN_STATUSES,
    ).exists()
    if not reservation_exists:
        raise ValidationError({
            "detail": "Бронь камеры изменилась — повторите запуск отгрузки",
            "code": "camera_reservation_lost",
        })


def ensure_scale_entry_weight(
    order,
    user,
    *,
    reservation_id: int | None = None,
    camera: str | None = None,
) -> Shipment | None:
    """Зафиксировать первый реальный замер до начала погрузки.

    Сетевой GET выполняется до транзакции и блокировки заказа. Повторный старт
    той же машины использует уже сохранённый замер: таймаут AI-сервиса не
    должен незаметно заменить вес пустого КАМАЗа более поздним показанием.
    """
    state = type(order).objects.filter(pk=order.pk).values(
        "transport_type", "truck_number", "scale_weighing_required"
    ).get()
    if (
        state["transport_type"] != "truck"
        or not state["scale_weighing_required"]
        or not scale.enabled()
    ):
        return None
    expected_truck_number = state["truck_number"]
    has_camera_reservation = reservation_id is not None and bool(camera)
    if not expected_truck_number.strip() and not has_camera_reservation:
        raise ValidationError({
            "detail": "Сначала укажите номер КАМАЗа",
            "code": "truck_number_required",
        })

    existing = Shipment.objects.filter(order_id=order.pk).first()
    if _matching_scale_entry(
        existing,
        expected_truck_number,
        reservation_id=reservation_id,
        camera=camera,
    ):
        return _store_scale_entry_weight(
            order,
            None,
            user,
            expected_truck_number=expected_truck_number,
            reservation_id=reservation_id,
            camera=camera,
        )

    reading = scale.read_truck_scale()
    return _store_scale_entry_weight(
        order,
        reading,
        user,
        expected_truck_number=expected_truck_number,
        reservation_id=reservation_id,
        camera=camera,
    )


def _save_scale_entry(
    shipment: Shipment,
    order,
    reading: scale.ScaleReading,
    user,
    *,
    reservation_id: int | None = None,
    camera: str | None = None,
) -> Shipment:
    previous_weight = shipment.weigh_in_kg
    previous_source = shipment.weigh_in_source
    shipment.truck_number = order.truck_number
    shipment.weigh_in_kg = reading.weight_kg
    shipment.weigh_in_source = Shipment.WeightSource.SCALE
    has_camera_reservation = reservation_id is not None and bool(camera)
    shipment.weigh_in_camera = camera if has_camera_reservation else ""
    shipment.weigh_in_session_id = (
        reservation_id if has_camera_reservation else None
    )
    shipment.weigh_out_kg = None
    shipment.net_weight_kg = None
    shipment.arrived_at = timezone.now()
    shipment.save()
    log_event(
        "weigh_in",
        f"Машина {order.truck_number}: входной вес {reading.weight_kg} кг",
        user=user,
        order=order,
        payload={
            "weight_kg": str(reading.weight_kg),
            "source": Shipment.WeightSource.SCALE,
            "age_seconds": str(reading.age_seconds),
            "scale_updated_at": reading.updated_at,
            "replaced_weight_kg": (
                str(previous_weight) if previous_weight is not None else None
            ),
            "replaced_source": previous_source,
        },
    )
    return shipment


@transaction.atomic
def _store_scale_entry_weight(
    order,
    reading: scale.ScaleReading | None,
    user,
    *,
    expected_truck_number: str,
    reservation_id: int | None = None,
    camera: str | None = None,
) -> Shipment:
    # Match counting.start's lock order: reservation first, order second.
    # The HTTP read happened before both, so no database lock spans network I/O.
    _lock_start_reservation(reservation_id, order.pk, camera)
    order = _locked(order)
    _require_transport(order, "truck")
    if order.status != "confirmed":
        raise ValidationError({
            "detail": "Взвесить КАМАЗ на въезде можно только до начала погрузки",
            "code": "invalid_status",
        })
    # Моноблок уже закрепляет физическую операцию за неизменяемой парой
    # order + camera reservation. Поэтому текстовый номер машины там не
    # является идентификатором весового замера. В остальных flow старое
    # требование номера сохраняется.
    has_camera_reservation = reservation_id is not None and bool(camera)
    if not order.truck_number.strip() and not has_camera_reservation:
        raise ValidationError({
            "detail": "Сначала укажите номер КАМАЗа",
            "code": "truck_number_required",
        })
    if order.truck_number != expected_truck_number:
        raise ValidationError({
            "detail": "Номер КАМАЗа изменился во время взвешивания — повторите замер",
            "code": "truck_number_changed_during_weighing",
        })

    shipment = Shipment.objects.select_for_update().filter(order=order).first()
    if _matching_scale_entry(
        shipment,
        order.truck_number,
        reservation_id=reservation_id,
        camera=camera,
    ):
        return shipment

    if reading is None:
        raise ValidationError({
            "detail": "Не удалось сохранить входной замер автомобильных весов",
            "code": "scale_entry_weight_required",
        })

    if shipment is None:
        shipment = Shipment(order=order)
    return _save_scale_entry(
        shipment,
        order,
        reading,
        user,
        reservation_id=reservation_id,
        camera=camera,
    )


def record_scale_arrival(order, user) -> Shipment:
    """Read outside a transaction, then atomically bind sample and arrival.

    Unlike calling ``ensure_scale_entry_weight`` followed by ``record_arrival``,
    this leaves no gap in which the truck number can change and make a stale
    scale value look as if it belonged to the replacement truck.
    """
    if not scale.enabled():
        raise ValidationError({
            "detail": "Автомобильные весы не настроены",
            "code": "truck_scale_disabled",
        })
    state = type(order).objects.filter(pk=order.pk).values(
        "transport_type", "truck_number"
    ).get()
    if state["transport_type"] != "truck":
        _require_transport(order, "truck")
    expected_truck_number = state["truck_number"]
    if not expected_truck_number.strip():
        raise ValidationError({
            "detail": "Сначала укажите номер КАМАЗа",
            "code": "truck_number_required",
        })

    existing = Shipment.objects.filter(
        order_id=order.pk,
        truck_number=expected_truck_number,
        weigh_in_source=Shipment.WeightSource.SCALE,
        weigh_in_kg__isnull=False,
    ).exists()
    reading = None if existing else scale.read_truck_scale()
    return _record_scale_arrival_locked(
        order,
        expected_truck_number,
        reading,
        user,
    )


@transaction.atomic
def _record_scale_arrival_locked(
    order,
    expected_truck_number: str,
    reading: scale.ScaleReading | None,
    user,
) -> Shipment:
    order = _locked(order)
    _require_transport(order, "truck")
    if order.status != "confirmed":
        raise ValidationError({
            "detail": "Машину можно принять только для подтверждённого заказа",
            "code": "invalid_status",
        })
    if order.truck_number != expected_truck_number:
        raise ValidationError({
            "detail": "Номер КАМАЗа изменился во время взвешивания — повторите замер",
            "code": "truck_number_changed_during_weighing",
        })

    shipment = Shipment.objects.select_for_update().filter(order=order).first()
    has_matching_entry = bool(
        shipment
        and shipment.truck_number == order.truck_number
        and shipment.weigh_in_source == Shipment.WeightSource.SCALE
        and shipment.weigh_in_kg is not None
    )
    if not has_matching_entry:
        if reading is None:
            raise ValidationError({
                "detail": "Не найден входной вес КАМАЗа",
                "code": "scale_entry_weight_required",
            })
        shipment = shipment or Shipment(order=order)
        _save_scale_entry(shipment, order, reading, user)

    order.status = "arrived"
    order.save(update_fields=["status"])
    log_event(
        "arrival",
        f"Машина {order.truck_number} прибыла",
        user=user,
        order=order,
        payload={
            "weigh_in_kg": str(shipment.weigh_in_kg),
            "source": Shipment.WeightSource.SCALE,
        },
    )
    return shipment


def read_scale_exit_if_required(order) -> BoundScaleReading | None:
    state = type(order).objects.filter(pk=order.pk).values(
        "transport_type",
        "truck_number",
        "shipment__weigh_in_source",
    ).get()
    if state["transport_type"] != "truck":
        return None
    source = state["shipment__weigh_in_source"]
    if source != Shipment.WeightSource.SCALE:
        # Уже начатые до внедрения весов машины завершаются по старому flow:
        # их прежний weigh_in мог быть расчётом мешков, считать по нему нельзя.
        return None
    return BoundScaleReading(
        truck_number=state["truck_number"],
        reading=scale.read_truck_scale(),
    )


def _record_scale_exit(
    order,
    shipment: Shipment,
    bound_reading: BoundScaleReading | None,
    user,
) -> None:
    if shipment.weigh_in_source != Shipment.WeightSource.SCALE:
        return
    if shipment.weigh_in_kg is None:
        raise ValidationError({
            "detail": "Не найден входной вес КАМАЗа",
            "code": "scale_entry_weight_required",
        })
    if bound_reading is None:
        raise ValidationError({
            "detail": "Перед завершением нужен свежий замер автомобильных весов",
            "code": "scale_exit_weight_required",
        })
    if (
        bound_reading.truck_number != order.truck_number
        or shipment.truck_number != order.truck_number
    ):
        raise ValidationError({
            "detail": "Номер КАМАЗа изменился во время взвешивания — повторите замер",
            "code": "truck_number_changed_during_weighing",
        })
    reading = bound_reading.reading
    if reading.weight_kg <= shipment.weigh_in_kg:
        raise ValidationError({
            "detail": (
                "Выходной вес должен быть больше входного: "
                f"{reading.weight_kg} кг ≤ {shipment.weigh_in_kg} кг"
            ),
            "code": "invalid_scale_weight_direction",
        })

    shipment.weigh_out_kg = reading.weight_kg
    shipment.net_weight_kg = reading.weight_kg - shipment.weigh_in_kg
    shipment.save(update_fields=["weigh_out_kg", "net_weight_kg"])
    log_event(
        "weigh_out",
        (
            f"Машина {shipment.truck_number}: выходной вес "
            f"{shipment.weigh_out_kg} кг, нетто {shipment.net_weight_kg} кг"
        ),
        user=user,
        order=shipment.order,
        payload={
            "weigh_in_kg": str(shipment.weigh_in_kg),
            "weigh_out_kg": str(shipment.weigh_out_kg),
            "net_weight_kg": str(shipment.net_weight_kg),
            "source": Shipment.WeightSource.SCALE,
            "age_seconds": str(reading.age_seconds),
            "scale_updated_at": reading.updated_at,
        },
    )


@transaction.atomic
def begin_camera_loading(
    order,
    camera: str,
    user,
    *,
    reservation_id: int | None = None,
):
    """Закрепить свободную камеру и перевести заказ в активную погрузку.

    Моноблок вызывает эту операцию перед запуском модели. Поэтому заказ из
    `confirmed` покидает «Ожидание въезда» только в момент фактического старта
    с выбранной камерой. Одна камера может принадлежать только одному живому
    заказу; ограничение продублировано частичным UNIQUE-индексом в PostgreSQL.
    """
    order = _locked(order)
    # Новый запуск возможен только для заказа, ожидающего въезда. Состояния
    # arrived/loading принимаем исключительно идемпотентно: когда эта же
    # камера уже была закреплена, а worker после перезапуска надо поднять снова.
    restoring_same_binding = (
        order.status in ("arrived", "loading")
        and order.loading_camera == camera
    )
    if order.status != "confirmed" and not restoring_same_binding:
        raise ValidationError({
            "detail": "Новая камера назначается только заказу в статусе «Ожидание въезда»",
            "code": "invalid_status",
        })

    _validate_loading_camera_available(order, camera)

    now = timezone.now()
    old_status = order.status
    shipment = getattr(order, "shipment", None)
    matching_scale_entry = _matching_scale_entry(
        shipment,
        order.truck_number,
        reservation_id=reservation_id,
        camera=camera,
    )
    if (
        order.transport_type == "truck"
        and old_status == "confirmed"
        and scale.enabled()
        and order.scale_weighing_required
        and not matching_scale_entry
    ):
        # Direct service calls and future adapters must obey the same boundary
        # as counting.start. Waiting until _do_ship would let an unweighed new
        # truck enter a live loading workflow and fail only at the exit gate.
        raise ValidationError({
            "detail": "Перед началом погрузки зафиксируйте входной вес КАМАЗа",
            "code": "scale_entry_weight_required",
        })
    if shipment is None:
        shipment = Shipment.objects.create(
            order=order,
            truck_number=order.truck_number if order.transport_type == "truck" else "",
        )

    if order.transport_type == "truck" and old_status == "confirmed":
        shipment.truck_number = order.truck_number
        # counting.start заранее сохраняет реальный замер без DB-lock. Прямые
        # старые вызовы сервиса оставляем совместимыми, но явно помечаем их
        # расчёт как estimated — выходное нетто по нему считаться не будет.
        if not matching_scale_entry:
            shipment.weigh_in_kg = estimated_load_kg(order)
            shipment.weigh_in_source = Shipment.WeightSource.ESTIMATED
        shipment.arrived_at = shipment.arrived_at or now
        log_event(
            "arrival",
            f"Машина {order.truck_number} принята через Моноблок",
            user=user,
            order=order,
            payload={
                "weigh_in_kg": str(shipment.weigh_in_kg),
                "source": shipment.weigh_in_source,
                "channel": "monoblock",
            },
        )

    shipment.loading_started_at = shipment.loading_started_at or now
    shipment.save()

    order.status = "loading"
    order.loading_camera = camera
    order.save(update_fields=["status", "loading_camera"])
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
    """Совместимый ручной приём, когда интеграция весов выключена."""
    order = _locked(order)
    _require_transport(order, "truck")
    if order.status != "confirmed":
        raise ValidationError(
            {"detail": "Машину можно принять только для подтверждённого заказа",
             "code": "invalid_status"}
        )
    if scale.enabled() and order.scale_weighing_required:
        raise ValidationError({
            "detail": "Используйте автоматический замер автомобильных весов",
            "code": "scale_entry_weight_required",
        })
    if weigh_in_kg is None:
        weigh_in_kg = estimated_load_kg(order)
        weigh_in_source = Shipment.WeightSource.ESTIMATED
    else:
        weigh_in_source = Shipment.WeightSource.MANUAL
    truck = order.truck_number
    order.status = "arrived"
    order.save(update_fields=["status"])
    shipment, _ = Shipment.objects.get_or_create(
        order=order, defaults={"truck_number": truck}
    )
    shipment.truck_number = truck
    shipment.weigh_in_kg = weigh_in_kg
    shipment.weigh_in_source = weigh_in_source
    shipment.weigh_out_kg = None
    shipment.net_weight_kg = None
    shipment.arrived_at = timezone.now()
    shipment.save()
    log_event("arrival", f"Машина {truck} прибыла", user=user, order=order,
              payload={
                  "weigh_in_kg": str(weigh_in_kg),
                  "source": weigh_in_source,
              })
    return shipment


@transaction.atomic
def record_count(order, bags, user):
    order = _locked(order)
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


def finish_loading(order, user):
    exit_reading = (
        read_scale_exit_if_required(order)
        if order.status == "loading" else None
    )
    return _finish_loading_locked(order, user, exit_reading)


@transaction.atomic
def _finish_loading_locked(order, user, exit_reading):
    order = _locked(order)
    _assert_device_order_camera(order, user)
    _require_transport(order, "truck")
    if order.status != "loading":
        raise ValidationError(
            {"detail": "Завершить можно только идущую загрузку", "code": "invalid_status"}
        )
    shipment = _require_shipment(order)
    _record_scale_exit(order, shipment, exit_reading, user)
    log_event("loading_done", "Загрузка завершена", user=user, order=order,
              payload={
                  "bags": shipment.bags_loaded,
                  "net_weight_kg": (
                      str(shipment.net_weight_kg)
                      if shipment.net_weight_kg is not None else None
                  ),
              })
    # Для оператора «отгружен» и «завершён» — один финальный этап. Не оставляем
    # заказ в техническом `loaded`: сразу фиксируем отгрузку, время, долг и
    # списание склада. `record_shipment` остаётся только для старых `loaded`.
    return _do_ship(
        order, shipment, user,
        f"Машина {shipment.truck_number}: отгрузка завершена",
    )


def _valid_ai_total(bags) -> bool:
    """Годное число мешков от воркера: целое неотрицательное, но не bool."""
    return not isinstance(bags, bool) and isinstance(bags, int) and bags >= 0


@transaction.atomic
def finish_ai_loading(order, bags: int, user, *, exit_reading=None):
    """Сохранить финальный AI-счёт и завершить отгрузку одним DB-действием.

    Воркер на ПК цеха — сторонний процесс, и его ответ может прийти пустым
    или битым. Раньше это роняло завершение посреди разбора AI-сессии: заказ
    оставался в ``loading`` с открытой сессией, а её наличие блокировало и
    ручное завершение, и откат — машина уже уехала, а заказ не закрыть.
    Поэтому негодное число не останавливает отгрузку: за факт берётся
    заказанное количество, а расхождение попадает в журнал.
    """
    order = _locked(order)
    _assert_device_order_camera(order, user)
    if order.status != "loading":
        raise ValidationError({
            "detail": "Завершить можно только идущую загрузку",
            "code": "invalid_status",
        })
    shipment = _require_shipment(order)
    _record_scale_exit(order, shipment, exit_reading, user)

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
        "Отгрузка завершена по финальному AI-подсчёту",
        user=user,
        order=order,
        payload={
            "bags": bags,
            "source": source,
            "net_weight_kg": (
                str(shipment.net_weight_kg)
                if shipment.net_weight_kg is not None else None
            ),
        },
    )
    label = (
        "Вагон: отгрузка завершена"
        if order.transport_type == "train"
        else f"Машина {shipment.truck_number}: отгрузка завершена"
    )
    return _do_ship(order, shipment, user, label)


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
    order = _locked(order)
    if order.status not in ("confirmed", "arrived", "loading", "loaded"):
        raise ValidationError({
            "detail": "Вручную завершить можно только подтверждённый или загружаемый заказ",
            "code": "invalid_status",
        })

    from apps.cameras.models import AiCountingSession
    if AiCountingSession.objects.filter(
        order=order,
        status__in=AiCountingSession.OPEN_STATUSES,
    ).exists():
        raise ValidationError({
            "detail": "Сначала остановите AI-подсчёт на посту погрузки",
            "code": "ai_session_active",
        })

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
            shipment.weigh_in_source = Shipment.WeightSource.ESTIMATED
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
    order = _locked(order)
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
    order = _locked(order)
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

    items = list(order.items.select_related("product"))
    deleted_products = [item.product_label for item in items if item.product_id is None]
    if deleted_products:
        raise ValidationError({
            "detail": "Нельзя восстановить склад: удалены товары — " + ", ".join(deleted_products),
            "code": "product_deleted",
        })

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
    """Списать со склада и зафиксировать отгрузку в долг. Общее для трака и вагона."""
    if order.transport_type == "truck":
        requires_new_scale_flow = bool(
            scale.enabled() and order.scale_weighing_required
        )
        has_scale_flow = shipment.weigh_in_source == Shipment.WeightSource.SCALE
        if requires_new_scale_flow and (
            not has_scale_flow or shipment.weigh_in_kg is None
        ):
            raise ValidationError({
                "detail": "Перед отгрузкой зафиксируйте входной вес КАМАЗа",
                "code": "scale_entry_weight_required",
            })
        if has_scale_flow:
            if shipment.truck_number != order.truck_number:
                raise ValidationError({
                    "detail": "Входной замер относится к другому номеру КАМАЗа",
                    "code": "scale_truck_number_mismatch",
                })
            if shipment.weigh_out_kg is None or shipment.net_weight_kg is None:
                # Manual status and legacy /ship/ must not bypass the second
                # sample after a physical scale entry has been captured.
                raise ValidationError({
                    "detail": "Перед отгрузкой зафиксируйте выходной вес КАМАЗа",
                    "code": "scale_exit_weight_required",
                })
            expected_net = shipment.weigh_out_kg - shipment.weigh_in_kg
            if expected_net <= 0 or shipment.net_weight_kg != expected_net:
                raise ValidationError({
                    "detail": "Нетто автомобильных весов повреждено или некорректно",
                    "code": "invalid_scale_net_weight",
                })
    for item in order.items.select_related("product").all():
        if item.product_id is None:
            raise ValidationError({
                "detail": f"Товар «{item.product_label}» удалён. Обновите состав заказа.",
                "code": "product_deleted",
            })
        deduct_stock(item.product, item.quantity, user, allow_negative=True)
    shipment.shipped_at = timezone.now()
    shipment.save()
    order.status = "shipped"
    order.payment_status = "unpaid"
    order.loading_camera = ""
    order.save(update_fields=["status", "payment_status", "loading_camera"])
    log_event("debt", f"Заказ отгружен в долг: {order.total_amount}", user=user, order=order,
              payload={"amount": str(order.total_amount), "intent": order.settlement_intent})
    bag_estimate = estimated_load_kg(order)
    log_event("shipment", label, user=user, order=order,
              payload={"bags_loaded": shipment.bags_loaded,
                       "bag_estimate_kg": str(bag_estimate),
                       "weigh_in_kg": (
                           str(shipment.weigh_in_kg)
                           if shipment.weigh_in_kg is not None else None
                       ),
                       "weigh_out_kg": (
                           str(shipment.weigh_out_kg)
                           if shipment.weigh_out_kg is not None else None
                       ),
                       "net_weight_kg": (
                           str(shipment.net_weight_kg)
                           if shipment.net_weight_kg is not None else None
                       ),
                       "weight_source": shipment.weigh_in_source})
    return shipment


def record_shipment(order, user):
    exit_reading = (
        read_scale_exit_if_required(order)
        if order.status == "loaded" else None
    )
    return _record_shipment_locked(order, user, exit_reading)


@transaction.atomic
def _record_shipment_locked(order, user, exit_reading):
    order = _locked(order)
    _require_transport(order, "truck")
    if order.status != "loaded":
        raise ValidationError(
            {"detail": "Выезд возможен только после завершения загрузки",
             "code": "invalid_status"}
        )
    shipment = _require_shipment(order)
    _record_scale_exit(order, shipment, exit_reading, user)
    return _do_ship(order, shipment, user, f"Машина {shipment.truck_number} выехала")


@transaction.atomic
def start_train_loading(order, user):
    """Вагон: старт сессии загрузки (без въезда и взвешивания)."""
    order = _locked(order)
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
    """Вагон: завершить загрузку и сразу отгрузить (авто)."""
    order = _locked(order)
    _require_transport(order, "train")
    if order.status != "loading":
        raise ValidationError(
            {"detail": "Завершить можно только идущую загрузку вагона",
             "code": "invalid_status"}
        )
    shipment = _require_shipment(order)
    log_event("loading_done", "Вагон: загрузка завершена", user=user, order=order,
              payload={"bags": shipment.bags_loaded})
    return _do_ship(order, shipment, user, "Вагон отгружен")
