from django.conf import settings
from django.db import models
from django.db.models import Q
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
    CURRENCIES = (("KZT", "KZT (тенге)"), ("USD", "USD (доллар)"))
    STATUSES = ["draft", "pending", "confirmed", "arrived",
                "loading", "loaded", "shipped", "rejected", "cancelled"]
    PAYMENT_STATUSES = ["unpaid", "partial", "settled"]
    SETTLEMENT_INTENTS = ["debt", "instant"]
    PAYMENT_METHODS = ["invoice", "kaspi", "cash", "debt"]
    TRANSPORT_TYPES = ["truck", "train"]

    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="orders"
    )
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="KZT")
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
    # Повторный заказ хранит ссылку на исходный документ. Это не связывает
    # их жизненные циклы: новый заказ получает собственные статусы, оплаты и
    # отгрузку, а удаление исходника только убирает ссылку.
    repeated_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="repeated_orders",
    )
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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["loading_camera"],
                condition=(
                    ~Q(loading_camera="")
                    & Q(status__in=["confirmed", "arrived", "loading"])
                    & Q(deleted_at__isnull=True)
                ),
                name="orders_one_active_order_per_loading_camera",
            ),
        ]

    @property
    def total_amount(self) -> Decimal:
        # Единственный источник суммы — договорная цена, зафиксированная в заказе.
        # У товара общей цены нет; неподтверждённая позиция пока стоит 0.
        return sum(
            (i.quantity * (i.unit_price if i.unit_price is not None else Decimal("0"))
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
    # История заказа живёт дольше номенклатуры. При физическом удалении товара
    # связь обнуляется, а снимок ниже продолжает описывать отгруженную позицию.
    product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="order_items",
    )
    product_label_snapshot = models.CharField(max_length=255, blank=True, default="")
    product_cv_class_snapshot = models.CharField(max_length=32, blank=True, default="")
    product_weight_kg_snapshot = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True)
    product_ask_truck_weight_snapshot = models.BooleanField(default=False)
    quantity = models.PositiveIntegerField()
    # Договорная цена за мешок, зафиксированная при подтверждении заказа.
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)

    @property
    def product_label(self):
        if self.product_label_snapshot:
            return self.product_label_snapshot
        return str(self.product) if self.product_id else "Удалённый товар"

    @property
    def product_weight_kg(self):
        if self.product_weight_kg_snapshot is not None:
            return self.product_weight_kg_snapshot
        return self.product.weight_kg if self.product_id else Decimal("0")

    @property
    def product_cv_class(self):
        if self.product_cv_class_snapshot:
            return self.product_cv_class_snapshot
        return self.product.cv_class if self.product_id else ""

    @property
    def product_ask_truck_weight(self):
        if self.product_label_snapshot:
            return self.product_ask_truck_weight_snapshot
        return self.product.ask_truck_weight if self.product_id else False

    def save(self, *args, **kwargs):
        # Заполняем снимок один раз: последующее переименование/удаление товара
        # не переписывает исторический заказ.
        if self.product_id and not self.product_label_snapshot:
            self.product_label_snapshot = str(self.product)
            self.product_cv_class_snapshot = self.product.cv_class
            self.product_weight_kg_snapshot = self.product.weight_kg
            self.product_ask_truck_weight_snapshot = self.product.ask_truck_weight
        super().save(*args, **kwargs)


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


class ApiPayInvoice(models.Model):
    """Счёт ApiPay, связанный с внутренней заявкой на оплату."""

    payment = models.OneToOneField(
        Payment, on_delete=models.CASCADE, related_name="apipay_invoice"
    )
    invoice_id = models.BigIntegerField(unique=True, null=True, blank=True)
    idempotency_key = models.CharField(max_length=191, unique=True)
    status = models.CharField(max_length=32, default="creating")
    channel = models.CharField(max_length=16, default="phone")
    phone_number = models.CharField(max_length=20, blank=True, default="")
    qr_token_url = models.URLField(max_length=1000, blank=True, default="")
    qr_image_url = models.URLField(max_length=1000, blank=True, default="")
    qr_expires_at = models.DateTimeField(null=True, blank=True)
    total_refunded = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    error_code = models.CharField(max_length=100, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    response_payload = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ApiPayRefund(models.Model):
    """Полный или частичный возврат по телефонному счёту ApiPay."""

    invoice = models.ForeignKey(
        ApiPayInvoice, on_delete=models.CASCADE, related_name="refunds"
    )
    refund_id = models.BigIntegerField(unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, default="pending")
    reason = models.CharField(max_length=500, blank=True, default="")
    kaspi_refund_id = models.CharField(max_length=100, blank=True, default="")
    error_code = models.CharField(max_length=100, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    response_payload = models.JSONField(default=dict, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="requested_apipay_refunds",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ApiPayWebhookEvent(models.Model):
    """Неизменяемый журнал принятых и проверенных уведомлений ApiPay."""

    body_sha256 = models.CharField(max_length=64, unique=True)
    event = models.CharField(max_length=100)
    invoice = models.ForeignKey(
        ApiPayInvoice, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="webhook_events",
    )
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


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
