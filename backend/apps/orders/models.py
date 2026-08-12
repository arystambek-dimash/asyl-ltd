from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q


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
    SETTLEMENT_INTENTS = ["pending", "debt", "instant"]
    PAYMENT_METHODS = ["pending", "invoice", "kaspi", "cash", "debt", "mixed"]
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
    # Выбор клиента. До выбора оплаты оба поля имеют значение pending;
    # затем settlement_intent хранит debt либо instant.
    payment_method = models.CharField(max_length=10, default="debt")
    truck_number = models.CharField(max_length=30, blank=True, default="")
    truck_number_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="truck_numbers_set",
    )
    # Rollout marker for the physical truck-scale workflow. Migration 0030
    # keeps every already existing order False, while newly created orders get
    # True. This lets active production trucks finish through their legacy
    # flow without letting new orders bypass entry/exit weighing.
    # db_default keeps rolling deploys compatible: an older application
    # process that does not know this column can still insert an order, and the
    # database marks that post-migration row as requiring the new workflow.
    scale_weighing_required = models.BooleanField(default=True, db_default=True)
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
        # LiveOrderManager добавляет deleted_at IS NULL к каждому запросу в
        # системе, поэтому индексы частичные: в них не попадает корзина, и
        # планировщик может брать их для любого списка заказов.
        indexes = [
            models.Index(
                fields=["-created_at"], name="order_live_created_idx",
                condition=Q(deleted_at__isnull=True),
            ),
            models.Index(
                fields=["status", "-created_at"], name="order_live_status_idx",
                condition=Q(deleted_at__isnull=True),
            ),
            models.Index(
                fields=["department", "-created_at"], name="order_live_dept_idx",
                condition=Q(deleted_at__isnull=True),
            ),
            # Корзина: обратное условие, её читает только раздел «Удалённые».
            models.Index(
                fields=["-deleted_at"], name="order_trash_idx",
                condition=Q(deleted_at__isnull=False),
            ),
        ]
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
        return sum(
            (p.net_amount for p in self.payments.all()
             if p.status == "confirmed"),
            Decimal("0"),
        )

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
    refunded_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    pending_refund_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
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

    class Meta:
        indexes = [
            # Журнал транзакций и выписки читают оплаты в обратном
            # хронологическом порядке. Без индекса это Seq Scan по всей
            # таблице на каждый запрос списка.
            models.Index(fields=["-paid_at"], name="payment_paid_at_desc_idx"),
            # Очередь кассы и сводный отчёт всегда фильтруют по этапу.
            models.Index(fields=["status"], name="payment_status_idx"),
            # Касса отчёта отбирает подтверждённые по способу оплаты
            # (reports._income_by_day, фильтр журнала транзакций) — метод
            # без статуса нигде не запрашивается, поэтому индекс составной.
            models.Index(fields=["status", "method"], name="payment_status_method_idx"),
        ]

    @property
    def net_amount(self) -> Decimal:
        return max(Decimal("0"), self.amount - self.refunded_amount)

    @property
    def available_for_refund(self) -> Decimal:
        if self.status != "confirmed":
            return Decimal("0")
        return max(
            Decimal("0"), self.net_amount - self.pending_refund_amount
        )


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
    # Provider-side transition time used to ignore delayed/out-of-order
    # webhook deliveries without relying on local receipt order.
    provider_status_at = models.DateTimeField(null=True, blank=True)
    # Independent provider-refund observation cursor. Invoice updated_at also
    # changes for status/QR operations and therefore cannot provide fair,
    # bounded round-robin discovery of refunds whose webhook was missed.
    refund_checked_at = models.DateTimeField(
        null=True, blank=True, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ApiPayRefund(models.Model):
    """Полный или частичный возврат по оплаченному счёту ApiPay."""

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


class PaymentRefund(models.Model):
    """Единый журнал возвратов: ApiPay или выдача из кассы."""

    METHODS = ["apipay", "cash"]
    STATUSES = ["pending", "completed", "failed"]

    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, related_name="payment_refunds"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20)
    status = models.CharField(max_length=20, default="pending")
    reason = models.CharField(max_length=500)
    provider_refund = models.OneToOneField(
        ApiPayRefund, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="payment_refund",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="requested_payment_refunds",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ApiPayWebhookEvent(models.Model):
    """Надёжный журнал принятых и проверенных уведомлений ApiPay."""

    body_sha256 = models.CharField(max_length=64, unique=True)
    semantic_key = models.CharField(
        max_length=191, unique=True, null=True, blank=True
    )
    event = models.CharField(max_length=100)
    provider_invoice_id = models.BigIntegerField(
        null=True, blank=True, db_index=True
    )
    invoice = models.ForeignKey(
        ApiPayInvoice, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="webhook_events",
    )
    payload = models.JSONField(default=dict)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True, default="")
    attempt_count = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
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
