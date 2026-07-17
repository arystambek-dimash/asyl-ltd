from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from apps.eventlog.services import log_event
from apps.warehouse.services import deduct_stock
from .models import Shipment


def _locked(order):
    """Перечитать заказ под блокировкой строки. Переходы статуса — это
    read-check-write: без блокировки двойной клик или две вкладки провели бы
    один шаг дважды (у отгрузки — двойное списание склада и двойной долг)."""
    return type(order).objects.select_for_update().get(pk=order.pk)


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
        (i.quantity * i.product.weight_kg for i in order.items.all()), Decimal("0")
    )


@transaction.atomic
def begin_camera_loading(order, camera: str, user):
    """Закрепить свободную камеру и перевести заказ в активную погрузку.

    Моноблок вызывает эту операцию перед запуском модели. Поэтому заказ из
    `confirmed` покидает «Ожидание въезда» только в момент фактического старта
    с выбранной камерой. Одна камера может принадлежать только одному живому
    заказу; ограничение продублировано частичным UNIQUE-индексом в PostgreSQL.
    """
    order = _locked(order)
    if order.status not in ("confirmed", "arrived", "loading"):
        raise ValidationError({
            "detail": "Камеру можно назначить только заказу перед погрузкой или во время неё",
            "code": "invalid_status",
        })

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

    now = timezone.now()
    old_status = order.status
    shipment = getattr(order, "shipment", None)
    if shipment is None:
        shipment = Shipment.objects.create(
            order=order,
            truck_number=order.truck_number if order.transport_type == "truck" else "",
        )

    if order.transport_type == "truck" and old_status == "confirmed":
        shipment.truck_number = order.truck_number
        shipment.weigh_in_kg = estimated_load_kg(order)
        shipment.arrived_at = now
        log_event(
            "arrival",
            f"Машина {order.truck_number} принята через Моноблок",
            user=user,
            order=order,
            payload={"weigh_in_kg": str(shipment.weigh_in_kg), "source": "monoblock"},
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
    # Въезд разрешён без оплаты: машина заезжает, затем склад грузит заказ,
    # а расчёт идёт после отгрузки. Вес спрашивается только для товаров с
    # флагом; если не передан — берём расчётный вес по мешкам.
    order = _locked(order)
    _require_transport(order, "truck")
    if order.status != "confirmed":
        raise ValidationError(
            {"detail": "Машину можно принять только для подтверждённого заказа",
             "code": "invalid_status"}
        )
    if weigh_in_kg is None:
        weigh_in_kg = estimated_load_kg(order)
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
              payload={"weigh_in_kg": str(weigh_in_kg)})
    return shipment


@transaction.atomic
def record_count(order, bags, user):
    order = _locked(order)
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


@transaction.atomic
def finish_loading(order, user):
    order = _locked(order)
    if order.status != "loading":
        raise ValidationError(
            {"detail": "Завершить можно только идущую загрузку", "code": "invalid_status"}
        )
    shipment = _require_shipment(order)
    order.status = "loaded"
    order.save(update_fields=["status"])
    log_event("loading_done", "Загрузка завершена", user=user, order=order,
              payload={"bags": shipment.bags_loaded})
    return shipment


@transaction.atomic
def rewind_loading(order, user):
    """Вернуть въехавший/загружаемый заказ обратно в ожидание въезда.

    Это отдельная бизнес-операция, а не голая ручная смена статуса: очищаем
    незавершённую отгрузку и освобождаем назначенную камеру. Работающую
    AI-сессию сначала обязан остановить её автор или администратор.
    """
    order = _locked(order)
    if order.status not in ("arrived", "loading"):
        raise ValidationError({
            "detail": "Вернуть в ожидание можно только заказ на этапе въезда или погрузки",
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
    order.status = "confirmed"
    order.loading_camera = ""
    order.save(update_fields=["status", "loading_camera"])
    log_event(
        "shipping_rewind",
        "Заказ возвращён в ожидание въезда",
        user=user,
        order=order,
        payload={"from": old, "to": "confirmed", "reset_bags": reset_bags},
    )
    return order


def _do_ship(order, shipment, user, label):
    """Списать со склада и зафиксировать отгрузку в долг. Общее для трака и поезда."""
    for item in order.items.select_related("product").all():
        deduct_stock(item.product, item.quantity, user, allow_negative=True)
    shipment.shipped_at = timezone.now()
    shipment.save()
    order.status = "shipped"
    order.payment_status = "unpaid"
    order.save(update_fields=["status", "payment_status"])
    log_event("debt", f"Заказ отгружен в долг: {order.total_amount}", user=user, order=order,
              payload={"amount": str(order.total_amount), "intent": order.settlement_intent})
    bag_estimate = estimated_load_kg(order)
    log_event("shipment", label, user=user, order=order,
              payload={"bags_loaded": shipment.bags_loaded,
                       "bag_estimate_kg": str(bag_estimate)})
    return shipment


@transaction.atomic
def record_shipment(order, user):
    order = _locked(order)
    _require_transport(order, "truck")
    if order.status != "loaded":
        raise ValidationError(
            {"detail": "Выезд возможен только после завершения загрузки",
             "code": "invalid_status"}
        )
    shipment = _require_shipment(order)
    # Выезд не взвешивается — просто фиксируем отгрузку.
    return _do_ship(order, shipment, user, f"Машина {shipment.truck_number} выехала")


@transaction.atomic
def start_train_loading(order, user):
    """Поезд: старт сессии загрузки (без въезда и взвешивания)."""
    order = _locked(order)
    _require_transport(order, "train")
    if order.status != "confirmed":
        raise ValidationError(
            {"detail": "Загрузку поезда можно начать только для подтверждённого заказа",
             "code": "invalid_status"}
        )
    shipment, _ = Shipment.objects.get_or_create(order=order)
    shipment.loading_started_at = timezone.now()
    shipment.save()
    order.status = "loading"
    order.save(update_fields=["status"])
    log_event("loading_start", "Поезд: начата загрузка", user=user, order=order)
    return shipment


@transaction.atomic
def finish_train_loading(order, user):
    """Поезд: завершить загрузку и сразу отгрузить (авто)."""
    order = _locked(order)
    _require_transport(order, "train")
    if order.status != "loading":
        raise ValidationError(
            {"detail": "Завершить можно только идущую загрузку поезда",
             "code": "invalid_status"}
        )
    shipment = _require_shipment(order)
    order.status = "loaded"
    order.save(update_fields=["status"])
    log_event("loading_done", "Поезд: загрузка завершена", user=user, order=order,
              payload={"bags": shipment.bags_loaded})
    return _do_ship(order, shipment, user, "Поезд отгружен")
