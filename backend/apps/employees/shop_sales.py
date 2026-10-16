"""Продажи из собственного остатка магазина, отдельно от заказов мельницы."""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.catalog.models import ClientPrice, Product
from apps.catalog.services import with_client_prices
from apps.clients.models import Client
from apps.common.money import money_string
from apps.orders.services import order_items_error
from apps.portal.serializers import MAX_PORTAL_ITEM_QUANTITY, MAX_PORTAL_ORDER_ITEMS
from apps.sales.access import scope_by_client_department

from .models import EmployeeShop, ShopSale, ShopSaleItem, ShopStock
from .shop import require_shop
from .shop_payments import (
    assert_sale_additions_permission,
    record_shop_sale_payment,
    sale_additions,
    sale_backdated_at,
    void_shop_sale_payment,
)
from .shop_stock import deduct_shop_stock, restore_shop_stock


def sale_orders(shop):
    return (
        ShopSale.objects.filter(shop=shop)
        .select_related("client__user", "payment")
        .prefetch_related("items__product")
        .alias(effective_sold_at=Coalesce("backdated_at", "created_at"))
        .order_by("-effective_sold_at", "-pk")
    )


def sale_catalog(shop):
    stock = ShopStock.objects.filter(shop=shop, product_id=OuterRef("pk")).values("bags")[:1]
    return with_client_prices(
        Product.objects.filter(is_active=True).annotate(
            stock_bags=Coalesce(Subquery(stock), 0, output_field=IntegerField()),
        ),
        shop.client_id,
        shop.client.currency,
    ).order_by("name", "color", "weight_kg")


def _locked_shop(user):
    if not user.has_perm_code("orders.create"):
        raise PermissionDenied("Нет права создавать заказы магазина")
    shop = (
        EmployeeShop.objects.select_for_update(of=("self",))
        .select_related("client")
        .filter(pk=require_shop(user).pk, is_active=True, employee__is_active=True)
        .first()
    )
    if shop is None:
        raise NotFound("У вас нет своего магазина")
    return shop


def _sale_items(items):
    if not items or len(items) > MAX_PORTAL_ORDER_ITEMS:
        raise ValidationError({"items": "Добавьте товары в пределах допустимого числа позиций"})
    if any(
        type(item["quantity"]) is not int or not 1 <= item["quantity"] <= MAX_PORTAL_ITEM_QUANTITY
        for item in items
    ):
        raise ValidationError({"items": "Укажите допустимое целое количество мешков"})
    products = {product.pk: product for product in Product.objects.filter(pk__in=[item["product"].pk for item in items])}
    if len(products) != len(items):
        raise ValidationError({"items": "Объедините повторяющиеся товары в одну строку"})
    rows = [{"product": products[item["product"].pk], "quantity": item["quantity"]} for item in items]
    if error := order_items_error(rows):
        raise ValidationError({"items": error})
    return rows


def _sale_client(user, client):
    if client is None:
        return None
    client = scope_by_client_department(
        Client.objects.all(), user,
    ).filter(pk=client.pk).first()
    if client is None:
        raise ValidationError({"client": "Клиент недоступен в вашем отделе"})
    return client


def _sale_unit_price(raw):
    if isinstance(raw, bool):
        raise ValidationError({"items": "Укажите допустимую цену"})
    try:
        price = ShopSaleItem._meta.get_field("unit_price").clean(raw, None)
    except DjangoValidationError as exc:
        raise ValidationError({"items": "Укажите допустимую цену с точностью до копеек"}) from exc
    if price is None or price <= 0:
        raise ValidationError({"items": "Цена должна быть больше нуля"})
    return price


@transaction.atomic
def place_shop_sale(user, *, idempotency_key, items, client=None, payment=None, backdate=None):
    """Сохранить продажу и её операции один раз для одного ключа запроса."""
    shop = _locked_shop(user)
    additions = sale_additions(payment=payment, backdate=backdate)
    requested_items = [
        {
            "product": item["product"].pk, "quantity": item["quantity"],
            **({"unit_price": money_string(_sale_unit_price(item["unit_price"]))} if "unit_price" in item else {}),
        }
        for item in items
    ]
    payload = {
        **additions,
        "client": client.pk if client else None,
        "items": sorted(
            requested_items,
            key=lambda item: (item["product"], item["quantity"]),
        ),
    }
    existing = ShopSale.objects.filter(shop=shop, idempotency_key=idempotency_key).first()
    if existing is not None:
        if existing.request_payload != payload:
            raise ValidationError({"idempotency_key": "Этот ключ уже использован для другой продажи"})
        return existing
    assert_sale_additions_permission(user, shop.client.currency, additions)
    rows = _sale_items(items)
    client = _sale_client(user, client)
    prices = dict(ClientPrice.objects.filter(
        client=shop.client, currency=shop.client.currency,
        product_id__in=[item["product"].pk for item in rows],
    ).values_list("product_id", "price"))
    prices.update({item["product"]: _sale_unit_price(item["unit_price"]) for item in requested_items if "unit_price" in item})
    unpriced = [str(item["product"]) for item in rows if prices.get(item["product"].pk, 0) <= 0]
    if unpriced:
        raise ValidationError({
            "detail": "Нет цены в прайсе магазина: " + ", ".join(unpriced) + ". Укажите цену в заказе.",
            "code": "shop_price_missing",
        })
    if backdate is None:
        deduct_shop_stock(shop, rows)
    sale = ShopSale.objects.create(
        shop=shop, client=client, created_by=user, currency=shop.client.currency,
        idempotency_key=idempotency_key, request_payload=payload,
        backdated_at=sale_backdated_at(additions), stock_deducted=backdate is None,
    )
    for item in rows:
        ShopSaleItem.objects.create(
            sale=sale, product=item["product"], quantity=item["quantity"],
            unit_price=prices[item["product"].pk], product_label_snapshot=str(item["product"]),
        )
    record_shop_sale_payment(sale, user, additions)
    return sale


@transaction.atomic
def cancel_shop_sale(user, sale_id):
    """Атомарно отменить оплату и вернуть только реально списанный товар."""
    shop = _locked_shop(user)
    sale = ShopSale.objects.select_for_update().filter(shop=shop, pk=sale_id).first()
    if sale is None:
        raise NotFound("Заказ магазина не найден")
    if sale.status == "cancelled":
        return sale
    payment = getattr(sale, "payment", None)
    if payment and payment.voided_at is None and not user.has_perm_code("payments.create"):
        raise PermissionDenied("Нет права отменять оплаты")
    if sale.stock_deducted:
        restore_shop_stock(shop, list(sale.items.select_related("product")))
    void_shop_sale_payment(sale)
    sale.status = "cancelled"
    sale.cancelled_at = timezone.now()
    sale.save(update_fields=["status", "cancelled_at"])
    return sale
