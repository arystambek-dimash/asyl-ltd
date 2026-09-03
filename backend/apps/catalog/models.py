from decimal import Decimal

from django.conf import settings
from django.db import connections, models, router, transaction


class Product(models.Model):
    COLORS = [("Red", "Красный"), ("Green", "Зелёный"), ("Blue", "Синий")]
    WEIGHTS = [
        (Decimal("2"), "2 кг"),
        (Decimal("5"), "5 кг"),
        (Decimal("10"), "10 кг"),
        (Decimal("25"), "25 кг"),
        (Decimal("50"), "50 кг"),
    ]

    name = models.CharField(max_length=100)
    color = models.CharField(max_length=10, choices=COLORS)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, choices=WEIGHTS)
    price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    ask_truck_weight = models.BooleanField(default=False)

    class Meta:
        unique_together = ("name", "color", "weight_kg")

    @property
    def cv_class(self):
        return f"{self.color}_{int(Decimal(self.weight_kg))}"

    def delete(self, using=None, keep_parents=False):
        """Preserve historical hard-delete semantics with rollout guards.

        The public catalogue API archives products. A few maintenance and
        historical flows still hard-delete an instance, and Django removes its
        dependent stock row before issuing the parent DELETE. Mark only this
        exact product in the current transaction so the warehouse guard can
        distinguish that collector operation from deleting an assignment.
        """
        database = using or router.db_for_write(type(self), instance=self)
        db_connection = connections[database]
        if db_connection.vendor != "postgresql":
            return super().delete(using=database, keep_parents=keep_parents)

        setting = "asyl.deleting_product_id"
        with transaction.atomic(using=database):
            with db_connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_setting(%s, TRUE)",
                    [setting],
                )
                previous = cursor.fetchone()[0] or ""
                cursor.execute(
                    "SELECT set_config(%s, %s, TRUE)",
                    [setting, str(self.pk)],
                )
            try:
                return super().delete(
                    using=database,
                    keep_parents=keep_parents,
                )
            finally:
                if not db_connection.needs_rollback:
                    with db_connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT set_config(%s, %s, TRUE)",
                            [setting, previous],
                        )

    @property
    def stock(self):
        """Compatibility bridge for the pre-multi-warehouse API.

        Once a product can be held in several warehouses, legacy callers still
        need a deterministic single row. Prefer the active business default,
        then a rollback-created main row, then the first named warehouse.
        New stock logic must use ``stock_items`` and an explicit warehouse.
        """
        cache = getattr(self, "_prefetched_objects_cache", {})
        if "stock_items" in cache:
            items = list(cache["stock_items"])
        else:
            items = list(self.stock_items.select_related("warehouse"))
        items.sort(
            key=lambda item: (
                0
                if item.warehouse is not None and item.warehouse.is_default
                else 1
                if item.warehouse is None
                else 2,
                item.warehouse.name if item.warehouse is not None else "",
                item.warehouse_id or 0,
                item.pk,
            )
        )
        item = items[0] if items else None
        if item is None:
            raise AttributeError("Product has no stock item")
        return item

    def __str__(self):
        color = dict(self.COLORS).get(self.color, self.color)
        return f"{self.name} · {color} {int(self.weight_kg)} кг"


class ClientPrice(models.Model):
    CURRENCIES = (("KZT", "KZT (тенге)"), ("USD", "USD (доллар)"))

    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="prices")
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="client_prices")
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="KZT")
    price = models.DecimalField(max_digits=12, decimal_places=2)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="set_client_prices")

    class Meta:
        unique_together = ("client", "product", "currency")
