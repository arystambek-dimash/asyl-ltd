"""Least-privilege reference projection used by the staff order form."""

from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.sales.access import scope_by_client_department
from apps.sales.models import Department
from apps.warehouse.models import Warehouse


def build_order_form_options(user) -> dict:
    """Return only fields needed to create or edit an order.

    The generic client, catalog and store APIs intentionally keep their own
    view permissions.  A user allowed to operate on orders still needs a small
    cross-domain projection to choose valid foreign keys, but does not need
    bank details, client debt, store schedules or CV metadata.
    """
    clients = scope_by_client_department(
        Client.objects.select_related("user"),
        user,
    ).only(
        "id",
        "user__first_name",
        "user__last_name",
        "user__username",
        "company_name",
        "phone",
        "currency",
    ).order_by("id")
    products = (
        Product.objects.filter(is_active=True)
        .prefetch_related("stock_items__warehouse")
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
    compatibility_warehouse = next(
        (warehouse for warehouse in warehouses if warehouse.code == "main"),
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
            }
            for client in clients
        ],
        "products": [
            _product_option(product, compatibility_warehouse) for product in products
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


def _product_stock(product, compatibility_warehouse=None):
    """Return bags and the effective warehouse for a Phase-1 stock row."""
    try:
        stock = product.stock
    except AttributeError:
        return 0, None, None
    warehouse = stock.warehouse or compatibility_warehouse
    return (
        stock.bags,
        warehouse.pk if warehouse is not None else None,
        warehouse.name if warehouse is not None else None,
    )


def _product_option(product, compatibility_warehouse):
    bags, warehouse_id, warehouse_name = _product_stock(
        product,
        compatibility_warehouse,
    )
    return {
        "id": product.id,
        "label": str(product),
        "available_bags": bags,
        "warehouse": warehouse_id,
        "warehouse_name": warehouse_name,
    }
