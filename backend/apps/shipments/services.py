from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from apps.eventlog.services import log_event
from apps.warehouse.services import deduct_stock
from .models import Shipment


@transaction.atomic
def record_arrival(order, weigh_in_kg, user, debt_override=False):
    if order.status not in ("confirmed", "paid"):
        raise ValidationError(
            {"detail": "Машину можно принять только для подтверждённого заказа",
             "code": "invalid_status"}
        )
    if not order.is_fully_paid:
        may_override = user is not None and user.has_perm_code("shipping.debt_override")
        if not (debt_override and may_override):
            raise ValidationError(
                {"detail": "Заказ не оплачен — въезд запрещён", "code": "payment_required"}
            )
        order.debt_override = True
        order.debt_override_by = user
        log_event("debt_override",
                  f"Отгрузка в долг разрешена ({user.username})",
                  user=user, order=order)
    truck = order.truck_number
    order.status = "arrived"
    order.save(update_fields=["status", "debt_override", "debt_override_by"])
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
            {"detail": "Загрузку можно начать только после прибытия", "code": "invalid_status"}
        )
    order.status = "loading"
    order.save(update_fields=["status"])
    log_event("loading_start", "Начата загрузка", user=user, order=order)
    return order.shipment


@transaction.atomic
def record_count(order, bags, user):
    if order.status == "arrived":
        order.status = "loading"
        order.save(update_fields=["status"])
        log_event("loading_start", "Начата загрузка", user=user, order=order)
    elif order.status != "loading":
        raise ValidationError(
            {"detail": "Подсчёт мешков возможен только во время загрузки",
             "code": "invalid_status"}
        )
    shipment = order.shipment
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
    order.status = "loaded"
    order.save(update_fields=["status"])
    log_event("loading_done", "Загрузка завершена", user=user, order=order,
              payload={"bags": order.shipment.bags_loaded})
    return order.shipment


@transaction.atomic
def record_shipment(order, user):
    if order.status != "loaded":
        raise ValidationError(
            {"detail": "Выезд возможен только после завершения загрузки",
             "code": "invalid_status"}
        )
    shipment = order.shipment
    # Выезд не взвешивается — просто фиксируем отгрузку.
    # Списываем по позициям заказа. Выезд должен пройти даже при нехватке:
    # остаток уходит в минус с предупреждением в журнале.
    for item in order.items.select_related("product").all():
        deduct_stock(item.product, item.quantity, user, allow_negative=True)
    shipment.shipped_at = timezone.now()
    shipment.save()
    order.status = "shipped"
    order.save(update_fields=["status"])
    bag_estimate = sum(
        (i.quantity * i.product.weight_kg for i in order.items.all()), Decimal("0")
    )
    log_event("shipment", f"Машина {shipment.truck_number} выехала",
              user=user, order=order,
              payload={"bags_loaded": shipment.bags_loaded,
                       "bag_estimate_kg": str(bag_estimate)})
    return shipment
