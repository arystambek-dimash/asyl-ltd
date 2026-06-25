from django.conf import settings
from django.db import models
from decimal import Decimal


class Order(models.Model):
    STATUSES = ["draft", "pending", "confirmed", "arrived",
                "loading", "loaded", "shipped", "rejected", "cancelled"]
    PAYMENT_STATUSES = ["unpaid", "partial", "settled"]
    SETTLEMENT_INTENTS = ["debt", "instant"]
    TRANSPORT_TYPES = ["truck", "train"]

    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="orders"
    )
    transport_type = models.CharField(max_length=10, default="truck")
    store = models.ForeignKey(
        "clients.Store", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="orders",
    )
    status = models.CharField(max_length=20, default="draft")
    payment_status = models.CharField(max_length=20, default="unpaid")
    settlement_intent = models.CharField(max_length=20, default="debt")
    truck_number = models.CharField(max_length=30, blank=True, default="")
    truck_number_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="truck_numbers_set",
    )
    arrival_date = models.DateField(null=True, blank=True)
    debt_requested = models.BooleanField(default=False)
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
        # Цена за мешок — зафиксированная договорная (unit_price); пока не задана,
        # используем базовую цену товара (для черновиков и обратной совместимости).
        return sum(
            (i.quantity * (i.unit_price if i.unit_price is not None else i.product.price)
             for i in self.items.all()),
            Decimal("0"),
        )

    @property
    def paid_total(self) -> Decimal:
        return sum((p.amount for p in self.payments.all() if p.status == "confirmed"), Decimal("0"))

    @property
    def is_fully_paid(self) -> bool:
        return self.total_amount > 0 and self.paid_total >= self.total_amount

    @property
    def remaining_amount(self) -> Decimal:
        return self.total_amount - self.paid_total


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    # Договорная цена за мешок, зафиксированная при подтверждении заказа.
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)


class Payment(models.Model):
    METHODS = ["cash", "card", "kaspi", "debt"]
    STATUSES = ["pending", "confirmed", "rejected"]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, default="cash")
    status = models.CharField(max_length=10, default="confirmed")
    paid_at = models.DateTimeField(auto_now_add=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="recorded_payments",
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="confirmed_payments",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)


class StatusChangeRequest(models.Model):
    STATUSES = ["pending", "approved", "rejected"]

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="status_requests")
    to_status = models.CharField(max_length=20)
    status = models.CharField(max_length=10, default="pending")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="status_change_requests",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="status_change_decisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
