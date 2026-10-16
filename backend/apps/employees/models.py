from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from decimal import Decimal

from apps.common.money import CURRENCY_CHOICES, DEFAULT_CURRENCY


class Employee(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="employee"
    )
    phone = models.CharField(max_length=50, blank=True, default="")
    position = models.CharField(max_length=100, blank=True, default="")
    sales_department = models.ForeignKey(
        "sales.Department",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="sales_employees",
    )
    permissions = models.ManyToManyField(
        "rbac.Permission",
        blank=True,
        related_name="employees"
    )
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        update_fields = kwargs.get("update_fields")
        if update_fields is None or "is_active" in update_fields:
            self._sync_user_active()

    def _sync_user_active(self):
        # Вход в систему следует флагу сотрудника: выключенный сотрудник
        # не должен логиниться, откуда бы флаг ни поменяли (API, админка).
        get_user_model().objects.filter(pk=self.user_id).exclude(
            is_active=self.is_active
        ).update(is_active=self.is_active)
        if self._meta.get_field("user").is_cached(self):
            self.user.is_active = self.is_active

    @property
    def name(self) -> str:
        return self.user.get_full_name() or self.user.username

    def __str__(self):
        return self.name


class EmployeeShop(models.Model):
    """Торговая точка сотрудника с отдельными остатками.

    ``client`` получает обычные заказы пополнения с мельницы; ``warehouse`` —
    склад мельницы по умолчанию. История прежних заказов сохраняется.
    """

    employee = models.OneToOneField(
        Employee, on_delete=models.CASCADE, related_name="shop"
    )
    client = models.OneToOneField(
        "clients.Client", on_delete=models.CASCADE, related_name="employee_shop"
    )
    warehouse = models.ForeignKey(
        "warehouse.Warehouse", on_delete=models.PROTECT, related_name="employee_shops"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.client.name


class ShopStock(models.Model):
    shop = models.ForeignKey(EmployeeShop, on_delete=models.PROTECT, related_name="stock")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    bags = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["shop", "product"], name="shop_stock_product_uniq")]


class ShopSale(models.Model):
    STATUSES = [("completed", "Завершён"), ("cancelled", "Отменён")]

    shop = models.ForeignKey(EmployeeShop, on_delete=models.PROTECT, related_name="sales")
    client = models.ForeignKey("clients.Client", null=True, blank=True, on_delete=models.SET_NULL)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default=DEFAULT_CURRENCY)
    status = models.CharField(max_length=12, choices=STATUSES, default="completed")
    idempotency_key = models.UUIDField()
    request_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["shop", "idempotency_key"], name="shop_sale_request_uniq")]

    @property
    def bags(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def total_amount(self):
        return sum((item.total_amount for item in self.items.all()), Decimal("0"))


class ShopSaleItem(models.Model):
    sale = models.ForeignKey(ShopSale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    product_label_snapshot = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="shop_sale_quantity_positive"),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0), name="shop_sale_price_nonnegative"),
        ]

    @property
    def product_label(self):
        return self.product_label_snapshot

    @property
    def total_amount(self):
        return self.quantity * self.unit_price

    def save(self, *args, **kwargs):
        if not self.product_label_snapshot:
            self.product_label_snapshot = str(self.product)
        super().save(*args, **kwargs)


class ShopReplenishment(models.Model):
    """Приход от обычной отгрузки. Снимок позиций переживает её откат."""

    order = models.OneToOneField("orders.Order", on_delete=models.PROTECT, related_name="shop_replenishment")
    shop = models.ForeignKey(EmployeeShop, on_delete=models.PROTECT, related_name="replenishments")
    received_at = models.DateTimeField(auto_now_add=True)
    reversed_at = models.DateTimeField(null=True, blank=True)


class ShopReplenishmentItem(models.Model):
    replenishment = models.ForeignKey(ShopReplenishment, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["replenishment", "product"], name="shop_receipt_product_uniq")]
