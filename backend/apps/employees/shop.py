"""Магазин сотрудника: включение в карточке сотрудника и заказы «сразу со склада».

Магазин — покупатель (``EmployeeShop.client``): цены — его прайс (ставит
бухгалтер, как клиентам), неоплаченное — долг магазина в кассе. Заказ не идёт
грузчику: подтверждается по прайсу и сразу отгружается со склада магазина.
Свой заказ сотрудник отменяет в течение часа, пока по нему нет денег, — по тому
же правилу, что грузчик свою отгрузку.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.catalog.models import ClientPrice, Product
from apps.catalog.services import with_client_prices
from apps.clients.assignment import move_client_department
from apps.clients.managers import sync_user_names
from apps.clients.models import Client
from apps.common.money import sum_by_currency
from apps.orders.debt import debt_by_currency, debt_fields, financial_orders
from apps.orders.models import Order, OrderItem, Payment
from apps.orders.services import confirm_order, order_items_error
from apps.sales.models import Department
from apps.shipments.services import loader_rollback_blocker, rollback_shipment, ship_from_warehouse
from apps.warehouse.services import resolve_warehouse

from .models import EmployeeShop
from .shop_access import active_shop

CANCEL_REASON = "Сотрудник отменил заказ своего магазина"


def default_shop_name(employee) -> str:
    return f"Магазин {employee.name}"


def require_shop(user) -> EmployeeShop:
    shop = active_shop(user)
    if shop is None:
        raise NotFound("У вас нет своего магазина")
    return shop


def _shop_department(employee) -> Department:
    """Отдел магазина — отдел сотрудника (иначе его заказ был бы «чужим»),
    без отдела — основной отдел продаж."""
    if employee.sales_department_id:
        return employee.sales_department
    department = (
        Department.objects.filter(is_active=True).order_by("-is_default", "created_at", "id").first()
    )
    if department is None:
        raise ValidationError({"shop": "Нет действующего отдела продаж для заказов магазина"})
    return department


@transaction.atomic
def set_employee_shop(employee, user, *, enabled: bool, name: str = "", warehouse=None) -> EmployeeShop | None:
    """Включить, настроить или выключить магазин сотрудника.

    Первое включение заводит покупателя-магазин (клиента с выключенным входом в
    портал) в отделе сотрудника. Выключение только прячет «Мой магазин»: клиент,
    заказы и долг остаются, а повторное включение берёт того же клиента.
    ``warehouse`` — склад, с которого списываются заказы; ``None`` — прежний,
    у нового магазина — основной.
    """
    shop = (
        EmployeeShop.objects.select_for_update(of=("self",))
        .select_related("client__user", "client__department", "warehouse")
        .filter(employee=employee)
        .first()
    )
    if not enabled:
        if shop is not None and shop.is_active:
            shop.is_active = False
            shop.save(update_fields=["is_active"])
        return shop

    name = " ".join(str(name or "").split()) or (shop.client.name if shop else default_shop_name(employee))
    warehouse = resolve_warehouse(warehouse if warehouse is not None else (shop.warehouse if shop else None))
    department = _shop_department(employee)
    if shop is None:
        client = Client.objects.create_with_user(
            first_name=name, last_name="", phone=employee.phone, department=department,
        )
        return EmployeeShop.objects.create(employee=employee, client=client, warehouse=warehouse)

    sync_user_names(shop.client.user, {"first_name": name, "last_name": ""})
    _move_with_employee(shop, department, user)
    shop.warehouse, shop.is_active = warehouse, True
    shop.save(update_fields=["warehouse", "is_active"])
    return shop


def _move_with_employee(shop: EmployeeShop, department: Department, user) -> None:
    if shop.client.department_id == department.pk:
        return
    shop.client = move_client_department(
        shop.client_id, department, user,
        f"Магазин «{shop.client.name}» переведён в отдел «{department.name}» вместе с сотрудником",
    )


@transaction.atomic
def follow_employee_department(employee, user) -> None:
    """Сотрудника перевели в другой отдел — его включённый магазин едет за ним:
    иначе заказ магазина оказался бы «чужим». Склад и название не трогает."""
    shop = (
        EmployeeShop.objects.select_for_update(of=("self",))
        .select_related("client")
        .filter(employee=employee, is_active=True)
        .first()
    )
    if shop is not None:
        _move_with_employee(shop, _shop_department(employee), user)


def shop_settings(shop: EmployeeShop | None) -> dict | None:
    """Настройки магазина в карточке сотрудника."""
    if shop is None:
        return None
    return {
        "enabled": shop.is_active,
        "client": shop.client_id,
        "name": shop.client.name,
        "warehouse": shop.warehouse_id,
        "warehouse_name": shop.warehouse.name,
    }


def shop_catalog(shop: EmployeeShop):
    """Товары для заказа с ценой из прайса магазина; без цены — ``price`` пустая."""
    client = shop.client
    return with_client_prices(
        Product.objects.filter(is_active=True), client.pk, client.currency,
    ).order_by("name", "color", "weight_kg")


def shop_orders(shop: EmployeeShop):
    """Заказы магазина, новые сверху (корзину отсекает менеджер заказов)."""
    return (
        Order.objects.filter(client=shop.client)
        .select_related("shipment")
        .prefetch_related("items__product", "payments")
        .order_by("-created_at", "-pk")
    )


def shop_summary(shop: EmployeeShop) -> dict:
    """Шапка «Моего магазина»: долг и взятое за текущий месяц."""
    client = shop.client
    orders = list(shop_orders(shop))
    month_start = timezone.localdate().replace(day=1)
    month = [
        order for order in financial_orders(orders)
        if timezone.localdate(order.sale_at) >= month_start
    ]
    return {
        # Клиент-магазин: его прайс сотрудник с правом на цены ставит сам из «Моего магазина».
        "client": client.pk,
        "name": client.name,
        "warehouse_name": shop.warehouse.name,
        "currency": client.currency,
        **debt_fields(debt_by_currency(orders), fallback=client.currency),
        "month_bags": sum(order.goods_bags for order in month),
        "month_amounts": {
            currency: str(amount)
            for currency, amount in sum_by_currency(month, lambda order: order.total_amount).items()
        },
    }


def cancel_blocker(order: Order, user) -> str:
    """Почему сотрудник не может сам отменить заказ своего магазина; пусто — можно."""
    if blocker := loader_rollback_blocker(order, user):
        return blocker
    # Деньги проверит и сам откат; здесь — чтобы не показывать кнопку зря.
    if any(
        payment.status == "confirmed" or payment.status in Payment.IN_PROGRESS_STATUSES
        for payment in order.payments.all()
    ):
        return "По заказу уже есть оплата — отмену оформляет старший в «Заказах»"
    return ""


@transaction.atomic
def place_shop_order(user, items: list[dict]) -> Order:
    """Заказ своего магазина: по прайсу магазина и сразу со склада, без грузчика.

    ``items`` — ``[{"product": Product, "quantity": int}]``. Товар без цены в
    прайсе магазина не заказать: цену ставит бухгалтер. Остаток склада не
    проверяется — как у любого заказа, списание может уйти в минус.
    """
    shop = (
        EmployeeShop.objects.select_for_update(of=("self",))
        .select_related("client__department", "warehouse")
        .filter(pk=require_shop(user).pk, is_active=True)
        .first()
    )
    if shop is None:
        raise NotFound("У вас нет своего магазина")
    if not items:
        raise ValidationError({"items": "Добавьте товары"})
    if error := order_items_error(items):
        raise ValidationError({"items": error})
    client = shop.client
    prices = dict(
        ClientPrice.objects.filter(
            client=client, currency=client.currency, product__in=[item["product"] for item in items],
        ).values_list("product_id", "price")
    )
    unpriced = [str(item["product"]) for item in items if not prices.get(item["product"].pk)]
    if unpriced:
        raise ValidationError({
            "detail": "Нет цены в прайсе магазина: " + ", ".join(unpriced) + ". Цену ставит бухгалтер.",
            "code": "shop_price_missing",
        })
    order = Order.objects.create(
        client=client,
        currency=client.currency,
        department=client.department.code if client.department_id else "",
        warehouse=resolve_warehouse(shop.warehouse),
        created_by=user,
        status="pending",
        is_shop_sale=True,
    )
    for item in items:
        OrderItem.objects.create(
            order=order, product=item["product"], quantity=item["quantity"], unit_price=prices[item["product"].pk],
        )
    confirm_order(order, user)
    ship_from_warehouse(order, user, label=f"Магазин сотрудника: списано со склада «{shop.warehouse.name}»")
    return order


@transaction.atomic
def cancel_shop_order(user, order_id) -> Order:
    """Сотрудник отменяет свой заказ: мешки возвращаются на склад, долг уходит."""
    shop = require_shop(user)
    order = shop_orders(shop).filter(pk=order_id).first()
    if order is None:
        raise NotFound("Заказ не найден")
    if blocker := loader_rollback_blocker(order, user):
        raise ValidationError({"detail": blocker, "code": "cancel_unavailable"})
    return rollback_shipment(order, user, target_status="cancelled", reason=CANCEL_REASON)
