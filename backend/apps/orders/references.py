"""Least-privilege reference projection used by the staff order form."""

from django.db.models import Prefetch

from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.sales.access import scope_by_client_department
from apps.sales.models import Department
from apps.warehouse.models import StockItem, Warehouse


def build_order_form_options(user) -> dict:
    """Return only fields needed to create or edit an order.

    The generic client, catalog and store APIs intentionally keep their own
    view permissions.  A user allowed to operate on orders still needs a small
    cross-domain projection to choose valid foreign keys, but does not need
    bank details, client debt, store schedules or CV metadata.
    """
    clients = scope_by_client_department(
        Client.objects.select_related("user", "department"),
        user,
    ).only(
        "id",
        "user__first_name",
        "user__last_name",
        "user__username",
        "company_name",
        "phone",
        "currency",
        "country",
        "department_id",
        "department__code",
        "department__name",
    ).order_by("id")
    products = (
        Product.objects.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "stock_items",
                queryset=StockItem.objects.filter(warehouse__is_active=True)
                .order_by("warehouse__name", "warehouse_id", "id"),
                to_attr="warehouse_stocks",
            )
        )
        .only(
            "id",
            "name",
            "color",
            "weight_kg",
        )
        .order_by("id")
    )
    warehouses = list(
        Warehouse.objects.filter(is_active=True).order_by("name", "id")
    )
    default_warehouse = next(
        (warehouse for warehouse in warehouses if warehouse.is_default),
        None,
    )
    stores = scope_by_client_department(
        Store.objects.all(),
        user,
        client_path="client",
    ).only(
        "id",
        "client_id",
        "name",
        "address",
    ).order_by("id")
    departments = Department.objects.filter(is_active=True).only(
        "id",
        "code",
        "name",
        "color",
        "is_default",
    )

    return {
        "clients": [
            {
                "id": client.id,
                "name": client.name,
                "company_name": client.company_name,
                "phone": client.phone,
                "currency": client.currency,
                # Страна номера машины по умолчанию (поле «Тягач» в форме).
                "country": client.country,
                "department_code": client.department.code if client.department_id else "",
                "department_name": client.department.name if client.department_id else "",
            }
            for client in clients
        ],
        "products": [
            _product_option(product, default_warehouse)
            for product in products
        ],
        "warehouses": [
            {
                "id": warehouse.id,
                "code": warehouse.code,
                "name": warehouse.name,
                "address": warehouse.address,
                "is_active": warehouse.is_active,
                "is_default": warehouse.is_default,
            }
            for warehouse in warehouses
        ],
        "stores": [
            {
                "id": store.id,
                "client": store.client_id,
                "name": store.name,
                "address": store.address,
            }
            for store in stores
        ],
        "departments": [
            {
                "id": department.id,
                "code": department.code,
                "name": department.name,
                "color": department.color,
                "is_default": department.is_default,
            }
            for department in departments
        ],
    }


def _product_option(product, default_warehouse) -> dict:
    """Товар формы заказа: остатки по складам и остаток без выбранного склада.

    ``available_bags`` форма показывает, пока склад не выбран: остаток склада
    по умолчанию, иначе первого склада с карточкой товара. Остатки приходят
    предзагруженными (``warehouse_stocks``) только по действующим складам.
    """
    stock_by_warehouse: dict[str, int] = {
        str(stock.warehouse_id): stock.bags for stock in product.warehouse_stocks
    }
    default_key = str(default_warehouse.pk) if default_warehouse is not None else None
    available = stock_by_warehouse.get(default_key, next(iter(stock_by_warehouse.values()), 0))
    return {
        "id": product.id,
        "label": str(product),
        "available_bags": available,
        "stock_by_warehouse": stock_by_warehouse,
    }
