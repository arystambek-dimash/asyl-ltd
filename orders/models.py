from django.conf import settings
from django.db import models
from decimal import Decimal


class Order(models.Model):
    STATUSES = ["draft", "confirmed", "paid", "arrived", "loading", "shipped", "cancelled"]

    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="orders"
    )
    status = models.CharField(max_length=20, default="draft")
    truck_number = models.CharField(max_length=30, blank=True, default="")
    debt_override = models.BooleanField(default=False)
    debt_override_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="debt_overrides",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_orders",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def total_amount(self) -> Decimal:
        return sum((i.quantity * i.product.price for i in self.items.all()), Decimal("0"))

    @property
    def paid_total(self) -> Decimal:
        return sum((p.amount for p in self.payments.all()), Decimal("0"))

    @property
    def is_fully_paid(self) -> bool:
        return self.total_amount > 0 and self.paid_total >= self.total_amount


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()


class Payment(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_at = models.DateTimeField(auto_now_add=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
