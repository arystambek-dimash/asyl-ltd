from django.conf import settings
from django.db import models
from django.db.models import Q


class Warehouse(models.Model):
    """A physical finished-goods warehouse.

    Phase 1 deliberately keeps stock ownership one-product-at-a-time across
    all warehouses.  The per-warehouse foreign keys are nullable so the
    previous application image can still insert rows during rollback.
    """

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="warehouse_one_default",
            ),
        ]

    def __str__(self):
        return self.name


class StockItem(models.Model):
    warehouse = models.ForeignKey(
        Warehouse,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="stock_items",
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.CASCADE, related_name="stock_items"
    )
    # IntegerField (не Positive): при списании по факту CV остаток может уйти в
    # минус, если посчитали больше, чем было на складе (с предупреждением).
    bags = models.IntegerField(default=0)

    class Meta:
        constraints = [
            # Rollback compatibility: the previous image still models this as
            # OneToOneField and must never observe two rows for one product.
            models.UniqueConstraint(
                fields=["product"],
                name="wh_stock_product_uniq_compat",
            ),
            models.UniqueConstraint(
                fields=["warehouse", "product"],
                name="wh_stock_wh_product_uniq",
            ),
        ]


class StockReceipt(models.Model):
    warehouse = models.ForeignKey(
        Warehouse,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="receipts",
    )
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    bags = models.PositiveIntegerField()
    received_at = models.DateTimeField(auto_now_add=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )


class StockMovement(models.Model):
    """История движений склада: каждое изменение остатка (+/-)."""

    REASONS = [
        ("adjustment", "Корректировка"),
        ("shipment", "Отгрузка"),
        ("shipment_correction", "Корректировка отгрузки"),
        ("receipt", "Приёмка"),
    ]

    warehouse = models.ForeignKey(
        Warehouse,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.CASCADE, related_name="movements"
    )
    delta = models.IntegerField()  # >0 добавлено, <0 списано
    balance_after = models.IntegerField()  # может быть отрицательным (списание в минус)
    reason = models.CharField(max_length=20, choices=REASONS, default="adjustment")
    note = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            # История движений только растёт и читается «сверху».
            models.Index(
                fields=["-created_at", "-id"], name="stockmovement_recent_idx"
            ),
        ]
