"""Дата и оплаты продаж магазина: отдельный учёт без кассы мельницы."""
from datetime import date

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.common.money import money_string
from apps.orders.backdate import backdate_moment
from apps.orders.services import assert_payment_method_allowed

from .models import ShopSalePayment


def _payment_method(method):
    if not isinstance(method, str) or method not in dict(ShopSalePayment.METHODS):
        raise ValidationError({"payment": "Недопустимый способ оплаты"})
    return method


def _payment_amount(raw):
    if isinstance(raw, bool):
        raise ValidationError({"payment": "Укажите допустимую сумму оплаты"})
    try:
        amount = ShopSalePayment._meta.get_field("amount").clean(raw, None)
    except (DjangoValidationError, TypeError, ValueError) as exc:
        raise ValidationError({"payment": "Укажите допустимую сумму оплаты с точностью до копеек"}) from exc
    if amount is None or amount <= 0:
        raise ValidationError({"payment": "Сумма оплаты должна быть больше нуля"})
    return amount


def sale_additions(*, payment=None, backdate=None):
    """Проверить входные данные сервиса и вернуть канонический снимок запроса."""
    if payment is not None and backdate is not None:
        raise ValidationError({"payment": "Оплату сразу нельзя совмещать с заказом задним числом"})
    payload = {}
    if payment is not None:
        if not isinstance(payment, dict):
            raise ValidationError({"payment": "Укажите сумму и способ оплаты"})
        payload["payment"] = {
            "amount": money_string(_payment_amount(payment.get("amount"))),
            "method": _payment_method(payment.get("method")),
        }
    if backdate is not None:
        if not isinstance(backdate, dict):
            raise ValidationError({"backdate": "Укажите дату продажи"})
        try:
            day = date.fromisoformat(str(backdate.get("date")))
        except (ValueError, TypeError) as exc:
            raise ValidationError({"backdate": "Дата — в формате ГГГГ-ММ-ДД"}) from exc
        if day > timezone.localdate():
            raise ValidationError({"backdate": "Дата не может быть в будущем"})
        paid = backdate.get("paid", False)
        if type(paid) is not bool:
            raise ValidationError({"backdate": "Укажите, оплачен ли заказ"})
        payload["backdate"] = {
            "date": day.isoformat(), "paid": paid,
            "payment_method": _payment_method(backdate.get("payment_method", "cash")),
        }
    return payload


def assert_sale_additions_permission(user, currency, additions):
    if "backdate" in additions and not user.has_perm_code("orders.edit"):
        raise PermissionDenied("Нет права вносить заказы задним числом")
    if ("payment" in additions or additions.get("backdate", {}).get("paid")) and not user.has_perm_code("payments.create"):
        raise PermissionDenied("Нет права принимать оплаты")
    if "payment" in additions:
        assert_payment_method_allowed(currency, additions["payment"]["method"])
    elif additions.get("backdate", {}).get("paid"):
        assert_payment_method_allowed(currency, additions["backdate"]["payment_method"])


def sale_backdated_at(additions):
    backdate = additions.get("backdate")
    return backdate_moment(date.fromisoformat(backdate["date"])) if backdate else None


def record_shop_sale_payment(sale, user, additions):
    """Вызывается внутри атомарного создания продажи под блокировкой магазина."""
    payment = additions.get("payment")
    backdate = additions.get("backdate", {})
    if payment is None and not backdate.get("paid"):
        return
    amount = _payment_amount(payment["amount"] if payment else sale.total_amount)
    if amount > sale.total_amount:
        raise ValidationError({"payment": "Оплата не может превышать сумму заказа"})
    ShopSalePayment.objects.create(
        sale=sale, amount=amount, method=payment["method"] if payment else backdate["payment_method"],
        paid_at=sale.backdated_at or timezone.now(), recorded_by=user,
    )


def void_shop_sale_payment(sale):
    ShopSalePayment.objects.filter(sale=sale, voided_at__isnull=True).update(voided_at=timezone.now())
