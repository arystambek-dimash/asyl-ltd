from django.conf import settings
from django.db import models
from decimal import Decimal


class OrderQuerySet(models.QuerySet):
    def deleted(self):
        return self.filter(deleted_at__isnull=False)


class LiveOrderManager(models.Manager):
    """Менеджер по умолчанию: удалённые (в корзине) заказы не видны нигде —
    ни в списках, ни в агрегатах, ни через related (client.orders/store.orders)."""
    def get_queryset(self):
        return OrderQuerySet(self.model, using=self._db).filter(deleted_at__isnull=True)


class Order(models.Model):
    STATUSES = ["draft", "pending", "confirmed", "arrived",
                "loading", "loaded", "shipped", "rejected", "cancelled"]
    PAYMENT_STATUSES = ["unpaid", "partial", "settled"]
    SETTLEMENT_INTENTS = ["debt", "instant"]
    PAYMENT_METHODS = ["invoice", "kaspi", "cash", "debt"]
    TRANSPORT_TYPES = ["truck", "train"]

    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="orders"
    )
    # Код динамического отдела продаж. Отдел выбирается непосредственно у заказа.
    department = models.CharField(max_length=50, default="main")
    transport_type = models.CharField(max_length=10, default="truck")
    store = models.ForeignKey(
        "clients.Store", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="orders",
    )
    status = models.CharField(max_length=20, default="draft")
    payment_status = models.CharField(max_length=20, default="unpaid")
    settlement_intent = models.CharField(max_length=20, default="debt")
    # Выбор клиента. settlement_intent сохраняем как совместимый финансовый
    # признак: debt для долга, instant для остальных способов.
    payment_method = models.CharField(max_length=10, default="debt")
    truck_number = models.CharField(max_length=30, blank=True, default="")
    truck_number_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="truck_numbers_set",
    )
    arrival_date = models.DateField(null=True, blank=True)
    # Короткая внутренняя заметка для оператора на детальной странице заказа.
    notes = models.TextField(blank=True, default="")
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
    # Камера, которую оператор занял под погрузку этого заказа (пост погрузки).
    # Пустая строка = камера не выбрана. Несколько заказов грузятся параллельно
    # на разных камерах.
    loading_camera = models.CharField(max_length=32, blank=True, default="")
    # Мягкое удаление: заказ уезжает в «Корзину», из отчётов исчезает,
    # но данные сохраняются и его можно восстановить.
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="deleted_orders",
    )

    # objects — только «живые» заказы (по умолчанию везде). all_objects — включая корзину.
    objects = LiveOrderManager()
    all_objects = OrderQuerySet.as_manager()

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

    @property
    def is_debt(self) -> bool:
        # Долг — только отгруженный заказ «в долг» с непогашенным остатком.
        # Черновик/на рассмотрении/в работе и моментальная оплата долгом не считаются.
        return (self.status == "shipped"
                and self.settlement_intent == "debt"
                and self.remaining_amount > 0)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    # Договорная цена за мешок, зафиксированная при подтверждении заказа.
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)


class Payment(models.Model):
    CASHIER_METHODS = ["cash", "kaspi", "invoice"]
    # Цепочка подтверждения: запрошена → принята (менеджер/оператор) →
    # подтверждена бухгалтером-кассой (только тогда деньги учтены).
    STATUSES = ["requested", "received", "confirmed", "rejected"]
    IN_PROGRESS_STATUSES = ["requested", "received"]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, default="cash")
    status = models.CharField(max_length=20, default="requested")
    # Примечание бухгалтера при внесении оплаты (видно в истории и на сверке).
    note = models.TextField(blank=True, default="")
    paid_at = models.DateTimeField(auto_now_add=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="recorded_payments",
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="received_payments",
    )
    received_at = models.DateTimeField(null=True, blank=True)
    # Финальное подтверждение кассира — фактическое поступление денег.
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
