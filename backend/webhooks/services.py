import re
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from orders.models import Order
from shipments.services import record_arrival, record_count, record_shipment
from .models import WebhookCall
from .templating import render_template


def normalize_plate(s: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", (s or "").upper())


def _find_order(plate_norm: str):
    if not plate_norm:
        return None
    for o in Order.objects.select_related("client").all():
        if normalize_plate(o.truck_number) == plate_norm:
            return o
    return None


def _build_context(camera, plate, order, decision, reason, bags, weight):
    net = None
    if order is not None:
        ship = getattr(order, "shipment", None)
        net = str(ship.net_weight_kg) if ship and ship.net_weight_kg is not None else None
    return {
        "camera_id": camera.camera_id,
        "decision": decision,
        "allowed": decision == "allow",
        "reason": reason or "",
        "order_id": order.id if order else None,
        "plate": plate,
        "client_name": order.client.name if order else "",
        "bags": bags,
        "weight_kg": weight,
        "net_weight_kg": net,
    }


def process_webhook(camera, body: dict) -> dict:
    plate_raw = body.get("plate", "")
    plate = normalize_plate(plate_raw)
    bags = body.get("bags")
    weight = body.get("weight_kg")
    user = None  # вебхук работает без вошедшего пользователя

    decision, reason, order = "deny", "", None
    with transaction.atomic():
        order = _find_order(plate)
        if order is None:
            reason = "Заказ по номеру не найден"
        else:
            try:
                if camera.kind == "entry":
                    record_arrival(order, Decimal(str(weight or 0)), user)
                elif camera.kind == "counter":
                    record_count(order, int(bags or 0), user)
                elif camera.kind == "exit":
                    record_shipment(order, Decimal(str(weight or 0)), user)
                else:
                    raise ValidationError({"detail": "Неизвестный тип камеры", "code": "bad_kind"})
                decision = "allow"
                order.refresh_from_db()
            except ValidationError as e:
                d = e.detail
                reason = d.get("detail") if isinstance(d, dict) else str(d)

        ctx = _build_context(camera, plate, order, decision, reason, bags, weight)
        try:
            response = render_template(camera.response_template, ctx)
        except ValueError:
            response = render_template("", ctx)

        WebhookCall.objects.create(
            camera=camera, plate=plate, payload_bags=bags,
            payload_weight=Decimal(str(weight)) if weight is not None else None,
            matched_order=order if (order and order.pk) else None,
            decision=decision, reason=reason or "",
            request_payload=body, response_payload=response,
        )
        camera.last_seen = timezone.now()
        camera.save(update_fields=["last_seen"])
    return response
