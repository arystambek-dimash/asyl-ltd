from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from apps.eventlog.services import log_event
from apps.warehouse.services import deduct_stock
from .models import Shipment


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
def record_arrival(order, weigh_in_kg, user):
    # Въезд разрешён без оплаты: машина заезжает, затем склад грузит заказ,
    # а расчёт идёт после отгрузки. Вес спрашивается только для товаров с
    # флагом; если не передан — берём расчётный вес по мешкам.
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
def start_loading(order, user):
    if order.status != "arrived":
        raise ValidationError(
            {"detail": "Загрузку можно начать только после въезда машины", "code": "invalid_status"}
        )
    shipment = _require_shipment(order)
    order.status = "loading"
    order.save(update_fields=["status"])
    log_event("loading_start", "Начата загрузка", user=user, order=order)
    return shipment


@transaction.atomic
def record_count(order, bags, user):
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
