from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Coalesce

from apps.common.money import CURRENCY_CHOICES, DEFAULT_CURRENCY

from .debt import DEBT_STATUS, counts_as_debt
from .statuses import CAMERA_BINDING_STATUSES


class OrderQuerySet(models.QuerySet):
    def deleted(self):
        """Orders still available in the user-facing recycle bin.

        A purged accounting document remains in ``all_objects`` so payments,
        shipment and external-provider reconciliation keep their immutable
        foreign-key target, but it must never reappear in the recycle bin.
        """
        return self.filter(
            deleted_at__isnull=False,
            purged_at__isnull=True,
        )


def money_ledger_q(prefix: str = "") -> Q:
    """Заказы денежной ленты — журнала кассы и выписок: живые и отгруженные из корзины.

    Отгруженный заказ — состоявшаяся продажа: товар уехал, деньги получены.
    Корзина прячет его из списков и аналитики, но продажу, оплаты и возвраты
    лента не теряет. У неотгруженного заказа в корзине денег нет: удалить его
    с деньгами не даёт ``services.assert_order_has_no_money``. ``prefix`` —
    путь до заказа от модели выборки (``"order__"``, ``"payment__order__"``).
    """
    live = Q(**{f"{prefix}deleted_at__isnull": True, f"{prefix}purged_at__isnull": True})
    return live | Q(**{f"{prefix}status": "shipped"})


class LiveOrderManager(models.Manager):
    """Менеджер по умолчанию: удалённые (в корзине) заказы не видны нигде —
    ни в списках, ни в агрегатах, ни через related (client.orders/store.orders)."""
    def get_queryset(self):
        return OrderQuerySet(self.model, using=self._db).filter(
            deleted_at__isnull=True,
            purged_at__isnull=True,
        )


class Order(models.Model):
    STATUSES = ["draft", "pending", "confirmed", "arrived",
                "loading", "loaded", "shipped", "rejected", "cancelled"]
    TRANSPORT_TYPES = ["truck", "train"]
    # День продажи — фактическая отгрузка; у старых заказов без даты отгрузки
    # (или без Shipment) — создание заказа. SQL-форма правила для фильтров
    # периода, ниже ``sale_at`` — то же правило для уже загруженного заказа.
    SALE_AT = Coalesce("shipment__shipped_at", "created_at")

    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="orders"
    )
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default=DEFAULT_CURRENCY)
    # Код динамического отдела продаж. Отдел выбирается непосредственно у заказа.
    department = models.CharField(max_length=50, default="main")
    transport_type = models.CharField(max_length=10, default="truck")
    store = models.ForeignKey(
        "clients.Store", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="orders",
    )
    # The source warehouse is independent from ``store`` (the client's delivery
    # location).
    warehouse = models.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.PROTECT,
        related_name="orders",
    )
    status = models.CharField(max_length=20, default="draft")
    rejection_reason = models.CharField(
        max_length=500, blank=True, default="", db_default=""
    )
    payment_status = models.CharField(max_length=20, default="unpaid")
    settlement_intent = models.CharField(max_length=20, default="debt")
    # Выбор клиента. До выбора оплаты оба поля имеют значение pending;
    # затем settlement_intent хранит debt либо instant.
    payment_method = models.CharField(max_length=10, default="debt")
    truck_number = models.CharField(max_length=30, blank=True, default="")
    # Полуприцеп фуры. Пара «тягач + прицеп» пишется только через
    # orders/transport.set_order_transport; у вагона прицепа нет.
    trailer_number = models.CharField(
        max_length=30, blank=True, default="", db_default=""
    )
    # Станция назначения вагонов (из отчёта об отгрузке: «Ст. Раустан»).
    # Номера вагонов — в shipments.ShipmentWagon.
    rail_station = models.CharField(
        max_length=120, blank=True, default="", db_default=""
    )
    truck_number_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="truck_numbers_set",
    )
    arrival_date = models.DateField(null=True, blank=True)
    # Короткая внутренняя заметка для оператора на детальной странице заказа.
    notes = models.TextField(blank=True, default="")
    debt_requested = models.BooleanField(default=False)
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
    # Мягкое удаление: заказ уезжает в «Корзину», из отчётов исчезает (кроме
    # денежной ленты отгруженного — money_ledger_q), данные сохраняются и его
    # можно восстановить.
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="deleted_orders",
    )
    # Permanent removal of a business document is logical whenever it has
    # operational history. The row stays addressable through ``all_objects``
    # for payments, shipment and provider reconciliation, while every
    # user-facing manager and the recycle bin hide it.
    purged_at = models.DateTimeField(null=True, blank=True)
    purged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="purged_orders",
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
            models.CheckConstraint(
                name="order_purged_only_from_trash",
                condition=(
                    Q(purged_at__isnull=True)
                    | Q(deleted_at__isnull=False)
                ),
            ),
            models.UniqueConstraint(
                fields=["loading_camera"],
                condition=(
                    ~Q(loading_camera="")
                    & Q(status__in=list(CAMERA_BINDING_STATUSES))
                    & Q(deleted_at__isnull=True)
                ),
                name="orders_one_active_order_per_loading_camera",
            ),
        ]

    @property
    def sale_at(self):
        shipment = getattr(self, "shipment", None)
        return getattr(shipment, "shipped_at", None) or self.created_at

    @property
    def ordered_bags(self) -> int:
        """Заказанное число мешков по всем позициям."""
        return sum(item.quantity for item in self.items.all())

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
        # Долг — непогашенный остаток отгруженного заказа (orders/debt.py):
        # способ расчёта не важен, товар уже у клиента. Черновик, заявка и заказ
        # в работе долгом не считаются.
        if self.status != DEBT_STATUS:
            # Позиции и оплаты не читаем: без prefetch это два лишних запроса
            # на каждый неотгруженный заказ выборки.
            return False
        return counts_as_debt(self.status, self.total_amount, self.paid_total)


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

    def fill_snapshot(self):
        """Заполнить снимок товара один раз: последующее переименование или
        удаление товара не переписывает исторический заказ. Вызывается из
        ``save()`` и перед ``bulk_create``, который ``save()`` не вызывает."""
        if self.product_id and not self.product_label_snapshot:
            self.product_label_snapshot = str(self.product)
            self.product_cv_class_snapshot = self.product.cv_class
            self.product_weight_kg_snapshot = self.product.weight_kg

    def save(self, *args, **kwargs):
        self.fill_snapshot()
        super().save(*args, **kwargs)


class Payment(models.Model):
    CASHIER_METHODS = ["cash", "kaspi", "remote", "invoice"]
    # Деньги уже у кассы: приём таким способом закрывается сразу, без очереди.
    # «remote» — отметка о ранее полученной удалённой оплате: счёт не выставляется.
    SETTLED_ON_RECORD = ("cash", "kaspi", "remote")
    # Служебный способ старых записей «в долг» — не деньги: в кассу, выписки
    # и отчёт бухгалтерии не входит.
    NON_MONEY_METHODS = ("debt",)
    # Цепочка подтверждения: запрошена → принята (менеджер/оператор) →
    # подтверждена бухгалтером-кассой (только тогда деньги учтены).
    STATUSES = ["requested", "received", "confirmed", "rejected"]
    IN_PROGRESS_STATUSES = ["requested", "received"]
    # День признания оплаты — подтверждение кассой; старые записи без отметки
    # подтверждения признаются днём внесения. Одно правило для выписок, отчёта
    # бухгалтерии и квитанции: SQL-форма здесь, ``recognized_at`` — для строки.
    RECOGNIZED_AT = Coalesce("confirmed_at", "paid_at")

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
            # Остаток по заказу (querysets.with_order_amounts) суммирует
            # подтверждённые оплаты заказа — коррелированный подзапрос на
            # каждый заказ списка должников; без составного индекса это
            # Seq Scan по всем оплатам на каждый заказ.
            models.Index(fields=["order", "status"], name="payment_order_status_idx"),
        ]

    @property
    def recognized_at(self):
        return self.confirmed_at or self.paid_at

    @property
    def author(self):
        """Кто отвечает за оплату: подтвердивший, принявший или внёсший её."""
        return self.confirmed_by or self.received_by or self.recorded_by

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


class PaymentRefundQuerySet(models.QuerySet):
    def unlinked_apipay_pending(self):
        """Резервы возврата ApiPay, ещё не сопоставленные с возвратом провайдера."""
        return self.filter(
            method="apipay",
            status="pending",
            provider_refund__isnull=True,
        )


class PaymentRefund(models.Model):
    """Единый журнал возвратов: ApiPay или выдача из кассы."""

    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, related_name="payment_refunds"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # День признания возврата — завершение; запасные поля оставляют видимыми
    # перенесённые старые возвраты. Одно правило для выписок и отчёта
    # бухгалтерии: SQL-форма здесь, ``recognized_at`` — для строки.
    RECOGNIZED_AT = Coalesce("completed_at", "updated_at", "created_at")

    # apipay | apipay_qr | cash. apipay_qr — возврат по Kaspi QR через ссылку
    # покупателю (ApiPayQrRefund).
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

    objects = PaymentRefundQuerySet.as_manager()

    @property
    def recognized_at(self):
        return self.completed_at or self.updated_at or self.created_at


class ApiPayQrRefund(models.Model):
    """Возврат по Kaspi QR: Kaspi вернёт деньги только после подтверждения покупателем.

    Касса выпускает ссылку «Возврат ApiPay», покупатель подтверждает её в Kaspi,
    после чего сервер один раз выполняет возврат (``execute``). Повтор execute —
    второй возврат живых денег, поэтому ``execute_requested_at`` фиксируется в
    базе ДО сетевого запроса и больше не снимается.
    """

    # Сессия ещё может дойти до денег: ждём покупателя или исход execute.
    ACTIVE_STATUSES = (
        "issuing", "awaiting_customer", "activating", "awaiting_scan",
        "customer_identified", "executing",
    )

    refund = models.OneToOneField(
        PaymentRefund, on_delete=models.CASCADE, related_name="qr_refund"
    )
    invoice = models.ForeignKey(
        ApiPayInvoice, on_delete=models.CASCADE, related_name="qr_refunds"
    )
    session_id = models.BigIntegerField(unique=True, null=True, blank=True)
    status = models.CharField(max_length=32, default="issuing", db_index=True)
    # Ссылка предъявительская и отдаётся ApiPay один раз: храним только шифром,
    # чтобы касса могла отправить её покупателю повторно.
    customer_url_encrypted = models.TextField(blank=True, default="")
    link_expires_at = models.DateTimeField(null=True, blank=True)
    client_name = models.CharField(max_length=120, blank=True, default="")
    operation_ref = models.CharField(max_length=255, blank=True, default="")
    # Покупки покупателя, когда подходящую нельзя выбрать автоматически.
    operations = models.JSONField(default=list, blank=True)
    execute_requested_at = models.DateTimeField(null=True, blank=True)
    refunded_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    receipt_url = models.URLField(max_length=1000, blank=True, default="")
    error_code = models.CharField(max_length=100, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    snapshot = models.JSONField(default=dict, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
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
    # Отдел, чьим секретом подписано событие: применяется только к его счетам.
    department = models.ForeignKey(
        "sales.Department", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="apipay_webhook_events",
    )
    payload = models.JSONField(default=dict)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True, default="")
    attempt_count = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)


class StatusChangeRequest(models.Model):
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
