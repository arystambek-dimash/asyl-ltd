"""Least-privilege reference projection used by the staff order form."""

from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.sales.access import scope_by_client_department
from apps.sales.models import Department


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
        .select_related("stock")
        .only(
            "id",
            "name",
            "color",
            "weight_kg",
            "stock__bags",
        )
        .order_by("id")
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
            {
                "id": product.id,
                "label": str(product),
                "available_bags": (
                    product.stock.bags
                    if hasattr(product, "stock")
                    else 0
                ),
            }
            for product in products
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
