from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from eventlog.services import log_event
from warehouse.services import deduct_stock
from .models import Shipment


@transaction.atomic
def record_arrival(order, truck_number, weigh_in_kg, user, debt_override=False):
    if order.status not in ("confirmed", "paid"):
        raise ValidationError(
            {"detail": "Машину можно принять только для подтверждённого заказа",
             "code": "invalid_status"}
        )
    if not order.is_fully_paid:
        may_override = user.has_perm_code("shipping.debt_override")
        if not (debt_override and may_override):
            raise ValidationError(
                {"detail": "Заказ не оплачен — въезд запрещён", "code": "payment_required"}
            )
        order.debt_override = True
        order.debt_override_by = user
        log_event("debt_override",
                  f"Отгрузка в долг разрешена ({user.username})",
                  user=user, order=order)
    order.truck_number = truck_number
    order.status = "arrived"
    order.save(update_fields=["truck_number", "status", "debt_override", "debt_override_by"])
    shipment, _ = Shipment.objects.get_or_create(
        order=order, defaults={"truck_number": truck_number}
    )
    shipment.truck_number = truck_number
    shipment.weigh_in_kg = weigh_in_kg
    shipment.arrived_at = timezone.now()
    shipment.save()
    log_event("arrival", f"Машина {truck_number} прибыла", user=user, order=order,
              payload={"weigh_in_kg": str(weigh_in_kg)})
    return shipment


@transaction.atomic
def record_loading(order, bags, user):
    if order.status != "arrived":
        raise ValidationError(
            {"detail": "Загрузка возможна только после прибытия", "code": "invalid_status"}
        )
    order.status = "loading"
    order.save(update_fields=["status"])
    shipment = order.shipment
    shipment.bags_loaded = bags
    shipment.save(update_fields=["bags_loaded"])
    log_event("loading", f"Загружено {bags} мешков", user=user, order=order,
              payload={"bags": bags})
    return shipment


@transaction.atomic
def record_shipment(order, weigh_out_kg, user):
    if order.status != "loading":
        raise ValidationError(
            {"detail": "Выезд возможен только во время загрузки", "code": "invalid_status"}
        )
    shipment = order.shipment
    net = abs(Decimal(weigh_out_kg) - shipment.weigh_in_kg)
    for item in order.items.select_related("product").all():
        deduct_stock(item.product, item.quantity, user)
    shipment.weigh_out_kg = weigh_out_kg
    shipment.net_weight_kg = net
    shipment.shipped_at = timezone.now()
    shipment.save()
    order.status = "shipped"
    order.save(update_fields=["status"])
    bag_estimate = sum(
        (i.quantity * i.product.weight_kg for i in order.items.all()), Decimal("0")
    )
    log_event("shipment", f"Выезд, нетто {net} кг", user=user, order=order,
              payload={"net_weight_kg": str(net),
                       "bag_estimate_kg": str(bag_estimate),
                       "discrepancy_kg": str(net - bag_estimate)})
    return shipment
