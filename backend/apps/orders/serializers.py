from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils.functional import cached_property
from rest_framework import serializers

from apps.clients.models import Client, Store
from apps.common.money import money_string
from apps.sales.access import scope_by_client_department
from apps.sales.models import Department

from .debt import order_overpaid
from .fixation import OrderFixationSerializer, assert_can_fixate, fixate_order
from .labels import payment_method_label
from .models import Order, OrderItem, Payment, StatusChangeRequest
from .services import set_order_department, set_transport_type
from .statuses import AWAITING_SHIPMENT_STATUSES, is_payment_open, public_status_label
from .transport import (
    clean_transport_pair,
    order_wagons,
    set_order_transport,
    transport_locked,
    transport_suggestions,
    transport_warning,
)


class OrderItemSerializer(serializers.ModelSerializer):
    product_label = serializers.CharField(read_only=True)
    cv_class = serializers.SerializerMethodField()
    quantity = serializers.IntegerField(min_value=1, max_value=2_147_483_647)
    unit_price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
        allow_null=True
    )
    price = serializers.SerializerMethodField()
    client_price = serializers.SerializerMethodField()
    weight_kg = serializers.SerializerMethodField()
    ask_truck_weight = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product",
            "product_label",
            "cv_class",
            "quantity",
            "price",
            "unit_price",
            "client_price",
            "weight_kg",
            "ask_truck_weight"
        ]
        extra_kwargs = {
            "product": {"required": True, "allow_null": False},
        }

    def get_cv_class(self, obj):
        return obj.product_cv_class

    def get_weight_kg(self, obj):
        return str(obj.product_weight_kg)

    def get_ask_truck_weight(self, obj):
        return obj.product_ask_truck_weight

    def get_price(self, obj):
        return str(obj.unit_price) if obj.unit_price is not None else None

    def get_client_price(self, obj):
        if obj.unit_price is not None:
            return None
        if obj.product_id is None:
            return None
        cache = self.context.setdefault("_client_prices", {})
        client = obj.order.client
        cache_key = (client.id, obj.order.currency)
        if cache_key not in cache:
            prefetched = getattr(client, "_prefetched_objects_cache", {}).get("prices")
            prices = prefetched if prefetched is not None else client.prices.all()
            cache[cache_key] = {
                cp.product_id: str(cp.price)
                for cp in prices if cp.currency == obj.order.currency
            }
        return cache[cache_key].get(obj.product_id)


class StatusChangeRequestSerializer(serializers.ModelSerializer):
    requested_by_name = serializers.SerializerMethodField()
    to_status_label = serializers.SerializerMethodField()

    class Meta:
        model = StatusChangeRequest
        fields = [
            "id",
            "order",
            "to_status",
            "to_status_label",
            "status",
            "requested_by",
            "requested_by_name",
            "decided_by",
            "created_at",
            "decided_at"
        ]

    def get_requested_by_name(self, obj):
        return obj.requested_by.username if obj.requested_by else None

    def get_to_status_label(self, obj):
        return public_status_label(obj.to_status)


def _username(user):
    return user.username if user else None


class PaymentSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.SerializerMethodField()
    received_by_name = serializers.SerializerMethodField()
    confirmed_by_name = serializers.SerializerMethodField()
    method_label = serializers.SerializerMethodField()
    currency = serializers.CharField(source="order.currency", read_only=True)
    provider = serializers.SerializerMethodField()
    client_name = serializers.CharField(source="order.client.name", read_only=True)
    effective_status = serializers.SerializerMethodField()
    available_for_refund = serializers.SerializerMethodField()
    refunds = serializers.SerializerMethodField()
    can_restore = serializers.SerializerMethodField()
    can_reopen = serializers.SerializerMethodField()
    can_issue = serializers.SerializerMethodField()
    confirmation_mode = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = ["id", "order", "currency", "amount", "method", "method_label", "status",
                  "note", "paid_at", "recorded_by", "recorded_by_name",
                  "received_by_name", "received_at",
                  "confirmed_by", "confirmed_by_name", "confirmed_at",
                  "client_name", "provider", "effective_status",
                  "refunded_amount", "pending_refund_amount",
                  "available_for_refund", "refunds",
                  "can_restore", "can_reopen", "can_issue", "confirmation_mode"]
        read_only_fields = ["order", "paid_at", "recorded_by", "confirmed_by"]

    def get_recorded_by_name(self, obj):
        return _username(obj.recorded_by)

    def get_received_by_name(self, obj):
        return _username(obj.received_by)

    def get_confirmed_by_name(self, obj):
        return _username(obj.confirmed_by)

    def get_method_label(self, obj):
        return payment_method_label(obj.method)

    def get_effective_status(self, obj):
        if obj.status in Payment.IN_PROGRESS_STATUSES:
            try:
                provider = obj.apipay_invoice
            except ObjectDoesNotExist:
                return obj.status
            if provider.status == "cancelling":
                return "cancellation_pending"
            if provider.status == "error":
                return "payment_error"
            return "awaiting_customer"
        if obj.status != "confirmed":
            return obj.status
        if obj.refunded_amount >= obj.amount:
            return "refunded"
        if obj.pending_refund_amount > 0:
            return "refund_pending"
        if obj.refunded_amount > 0:
            return "partially_refunded"
        return "confirmed"

    def get_available_for_refund(self, obj):
        return money_string(obj.available_for_refund)

    def _request_can(self, code):
        request = self.context.get("request")
        return request is None or request.user.has_perm_code(code)

    def get_can_reopen(self, obj):
        """Ошибочно подтверждённую оплату кассы можно вернуть на проверку.

        Те же условия, что у services.reopen_confirmed_payment: онлайн-оплату
        и оплату с возвратом так не откатывают — приход и возврат остаются в истории.
        """
        if not self._request_can("payments.confirm") or obj.status != "confirmed":
            return False
        if obj.refunded_amount > 0 or obj.pending_refund_amount > 0:
            return False
        try:
            obj.apipay_invoice
        except ObjectDoesNotExist:
            return True
        return False

    def get_can_restore(self, obj):
        if (
                not self._request_can("payments.confirm")
                or obj.status != "rejected"
        ):
            return False
        confirmed = sum(
            (
                payment.net_amount
                for payment in obj.order.payments.all()
                if payment.status == "confirmed"
            ),
            Decimal("0"),
        )
        reserved = sum(
            (
                payment.amount
                for payment in obj.order.payments.all()
                if payment.status in Payment.IN_PROGRESS_STATUSES
            ),
            Decimal("0"),
        )
        available = max(
            Decimal("0"),
            obj.order.total_amount - confirmed - reserved,
        )
        if obj.amount > available:
            return False
        try:
            invoice = obj.apipay_invoice
        except ObjectDoesNotExist:
            return True
        return not (
                invoice.invoice_id is not None
                and invoice.status in ("cancelled", "expired", "error", "superseded")
        )

    def get_can_issue(self, obj):
        if (
                not self._request_can("payments.create")
                or obj.status not in Payment.IN_PROGRESS_STATUSES
                or obj.method != "invoice"
        ):
            return False
        try:
            invoice = obj.apipay_invoice
        except ObjectDoesNotExist:
            return True
        return (
                invoice.invoice_id is None
                and not (
                invoice.channel == "qr"
                and invoice.status == "creating"
        )
        )

    def get_confirmation_mode(self, obj):
        try:
            obj.apipay_invoice
        except ObjectDoesNotExist:
            return "manual"
        return "automatic"

    def get_refunds(self, obj):
        return [
            {
                "id": row.pk,
                "amount": money_string(row.amount),
                "method": row.method,
                "status": row.status,
                "reason": row.reason,
                "requested_by_name": _username(row.requested_by),
                "completed_at": row.completed_at,
                "created_at": row.created_at,
            }
            for row in obj.payment_refunds.all()
        ]

    def get_provider(self, obj):
        try:
            invoice = obj.apipay_invoice
        except ObjectDoesNotExist:
            return None
        return {
            "invoice_id": invoice.invoice_id,
            "channel": invoice.channel,
            "status": invoice.status,
            "phone_number": invoice.phone_number or None,
            "qr_token_url": invoice.qr_token_url or None,
            "qr_image_url": invoice.qr_image_url or None,
            "qr_expires_at": invoice.qr_expires_at,
            "total_refunded": money_string(invoice.total_refunded),
            "available_for_refund": money_string(obj.available_for_refund),
            "refunds": [
                {
                    "id": row.refund_id,
                    "amount": money_string(row.amount),
                    "status": row.status,
                    "reason": row.reason,
                    "error_code": row.error_code or None,
                    "created_at": row.created_at,
                }
                for row in invoice.refunds.all()
            ],
        }


class DepartmentLabelMixin:
    def _department_code(self, obj):
        return obj.department

    def _department(self, code):
        if not hasattr(self, "_departments"):
            self._departments = {row.code: row for row in Department.objects.all()}
        return self._departments.get(code)

    def get_department_name(self, obj):
        code = self._department_code(obj)
        row = self._department(code)
        return row.name if row else (code or "Нет отдела")

    def get_department_color(self, obj):
        row = self._department(self._department_code(obj))
        return row.color if row else "#64748B"


class PaymentQueueSerializer(DepartmentLabelMixin, PaymentSerializer):
    client_name = serializers.CharField(source="order.client.name", read_only=True)
    department = serializers.CharField(source="order.department", read_only=True)
    department_name = serializers.SerializerMethodField()
    department_color = serializers.SerializerMethodField()
    order_status = serializers.CharField(source="order.status", read_only=True)
    store = serializers.IntegerField(source="order.store_id", read_only=True,
                                     allow_null=True)
    store_name = serializers.CharField(source="order.store.name", read_only=True,
                                       allow_null=True)

    class Meta(PaymentSerializer.Meta):
        fields = PaymentSerializer.Meta.fields + [
            "client_name", "department", "department_name", "department_color",
            "order_status", "store", "store_name"]

    def _department_code(self, obj):
        return obj.order.department


def _final_transport(instance, data) -> tuple[str, str]:
    """Пара номеров после правки заказа, проверенная по правилам транспорта.

    Непереданный номер остаётся прежним; при переходе на вагон прицеп
    снимается (см. ``set_transport_type``). Неизменённый исторический номер
    не перепроверяется.
    """
    transport = data.get("transport_type", instance.transport_type)
    return clean_transport_pair(
        transport,
        data.get("truck_number", instance.truck_number),
        data.get("trailer_number", "" if transport == "train" else instance.trailer_number),
        current=(instance.transport_type, instance.truck_number, instance.trailer_number),
    )


class ShipmentWagonSerializer(serializers.Serializer):
    """Вагон отгрузки по отчёту: номер, товар, мешки и вес."""

    number = serializers.CharField()
    product_label = serializers.CharField()
    bags = serializers.IntegerField()
    weight_kg = serializers.DecimalField(max_digits=10, decimal_places=2)


class OrderWagonsMixin:
    """Вагоны вагонного заказа — везде, где виден его номер (``shipment__wagons`` предзагружены)."""

    def get_wagons(self, order):
        return ShipmentWagonSerializer(order_wagons(order), many=True).data


class OrderSerializer(OrderWagonsMixin, DepartmentLabelMixin, serializers.ModelSerializer):
    client_department = serializers.CharField(
        source="client.department.code", read_only=True, default=""
    )
    client_department_name = serializers.CharField(
        source="client.department.name", read_only=True, default=""
    )
    items = OrderItemSerializer(many=True, allow_empty=False, max_length=100)
    transport_type = serializers.ChoiceField(choices=Order.TRANSPORT_TYPES, required=False)
    edit_reason = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=500,
    )
    status = serializers.CharField(read_only=True)
    loading_camera = serializers.CharField(read_only=True)
    payment_status = serializers.CharField(read_only=True)
    settlement_intent = serializers.ChoiceField(
        choices=Order.SETTLEMENT_INTENTS,
        required=False,
    )
    payment_method = serializers.CharField(read_only=True)
    total_amount = serializers.DecimalField(max_digits=30, decimal_places=2, read_only=True)
    paid_total = serializers.DecimalField(max_digits=30, decimal_places=2, read_only=True)
    remaining_amount = serializers.DecimalField(max_digits=30, decimal_places=2, read_only=True)
    # Окно оплаты для сотрудника считает сервер (statuses.is_payment_open):
    # фронт не повторяет правило статусов и показывает только открытые способы.
    payment_open = serializers.SerializerMethodField()
    payment_open_methods = serializers.SerializerMethodField()
    # «kaspi» в способах — свой терминал; Kaspi QR и счёт на телефон — запрос
    # денег (method=None), он открывается отдельно: только после отгрузки.
    payment_request_open = serializers.SerializerMethodField()
    overpaid_amount = serializers.SerializerMethodField()
    is_fully_paid = serializers.BooleanField(read_only=True)
    is_debt = serializers.BooleanField(read_only=True)
    client_name = serializers.CharField(source="client.name", read_only=True)
    client_phone = serializers.CharField(source="client.phone", read_only=True)
    warehouse_name = serializers.CharField(
        source="warehouse.name",
        read_only=True,
        allow_null=True,
        default=None,
    )
    weigh_in_kg = serializers.SerializerMethodField()
    bags_loaded = serializers.SerializerMethodField()
    bag_estimate_kg = serializers.SerializerMethodField()
    bag_weight_kg = serializers.SerializerMethodField()
    debt_override_by_name = serializers.SerializerMethodField()
    deleted_by_name = serializers.SerializerMethodField()
    pending_status_requests = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()
    pending_payments = serializers.SerializerMethodField()
    shipped_at = serializers.SerializerMethodField()
    wagons = serializers.SerializerMethodField()
    department = serializers.CharField(required=False, allow_blank=True)
    department_name = serializers.SerializerMethodField()
    department_color = serializers.SerializerMethodField()
    reviewed_at = serializers.DateTimeField(read_only=True)
    reviewed_by = serializers.PrimaryKeyRelatedField(read_only=True)
    currency = serializers.ChoiceField(choices=Order.CURRENCIES, required=False)
    # Источник шаблона передаётся только при создании. Сам заказ всё равно
    # создаётся обычной формой после ручной проверки менеджером.
    template_order = serializers.PrimaryKeyRelatedField(
        queryset=Order.objects.all(), write_only=True, required=False,
    )
    # Заказ задним числом: дата, статус и оплата проставляются одной
    # транзакцией с созданием (см. orders/fixation.py).
    backdate = OrderFixationSerializer(write_only=True, required=False)

    class Meta:
        model = Order
        fields = [
            "id",
            "client",
            "store",
            "warehouse",
            "warehouse_name",
            "client_name",
            "client_phone",
            "client_department",
            "client_department_name",
            "department",
            "department_name",
            "department_color",
            "status",
            "reviewed_at",
            "reviewed_by",
            "rejection_reason",
            "currency",
            "payment_status",
            "settlement_intent",
            "payment_method",
            "transport_type",
            "truck_number",
            "trailer_number",
            "rail_station",
            "wagons",
            "arrival_date",
            "notes",
            "items",
            "total_amount",
            "paid_total",
            "remaining_amount",
            "payment_open",
            "payment_open_methods",
            "payment_request_open",
            "overpaid_amount",
            "is_fully_paid",
            "is_debt",
            "debt_override",
            "debt_override_by_name",
            "pending_status_requests",
            "payments",
            "pending_payments",
            "weigh_in_kg",
            "bags_loaded",
            "bag_estimate_kg",
            "bag_weight_kg",
            "created_at",
            "shipped_at",
            "loading_camera",
            "repeated_from",
            "template_order",
            "backdate",
            "edit_reason",
            "deleted_at",
            "deleted_by_name",
        ]
        read_only_fields = [
            "debt_override",
            "repeated_from",
            "deleted_at",
            "rejection_reason",
            # Станцию и вагоны пишет отгрузка по отчёту о вагонах.
            "rail_station",
        ]
        extra_kwargs = {
            "truck_number": {"required": False},
            "trailer_number": {"required": False},
            "arrival_date": {"required": False, "allow_null": True},
            "store": {"required": False, "allow_null": True},
            "warehouse": {"required": False, "allow_null": True},
            "transport_type": {"required": False},
        }

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None:
            return fields
        fields["client"].queryset = scope_by_client_department(
            Client.objects.all(),
            user,
        )
        fields["store"].queryset = scope_by_client_department(
            Store.objects.all(),
            user,
            client_path="client",
        )
        fields["template_order"].queryset = scope_by_client_department(
            Order.objects.all(),
            user,
            client_path="client",
        )
        return fields

    def _shipment(self, obj):
        return getattr(obj, "shipment", None)

    def get_weigh_in_kg(self, obj):
        s = self._shipment(obj)
        return str(s.weigh_in_kg) if s and s.weigh_in_kg is not None else None

    def get_bags_loaded(self, obj):
        s = self._shipment(obj)
        return s.bags_loaded if s else 0

    def get_shipped_at(self, obj):
        # Заказ, отгруженный вручную (без поста), Shipment не имеет — тогда None.
        s = self._shipment(obj)
        return s.shipped_at if s else None

    def _first_item(self, obj):
        # items предзагружены — берём из кэша, .first() породил бы новый запрос.
        items = list(obj.items.all())
        return items[0] if items else None

    def get_bag_estimate_kg(self, obj):
        """Ожидаемый вес груза по факту камеры.

        Считаем по всем позициям: у смешанного заказа фасовки разные, и вес
        первой позиции, умноженный на все мешки, завышал число (30×50кг +
        20×25кг давало 2500 вместо 2000). Расчёт должен совпадать с итогом
        поста погрузки и не зависит от физического сервиса весов Grain.
        """
        items = list(obj.items.all())
        ordered = sum(item.quantity for item in items)
        full_weight = sum(
            (item.quantity * item.product_weight_kg for item in items), Decimal("0"))
        shipment = self._shipment(obj)
        bags = shipment.bags_loaded if shipment else 0
        if not ordered:
            return str(Decimal("0"))
        if bags == ordered:
            return str(full_weight)
        # Камера насчитала не столько, сколько заказано: состав недогруза
        # неизвестен, поэтому масштабируем средним весом мешка по заказу.
        return str(full_weight * Decimal(bags) / Decimal(ordered))

    def get_bag_weight_kg(self, obj):
        first = self._first_item(obj)
        per = first.product_weight_kg if first else Decimal("0")
        return str(per)

    def get_payment_open_methods(self, obj):
        return [
            method for method in Payment.CASHIER_METHODS
            if is_payment_open(obj.status, method=method)
        ]

    def get_payment_open(self, obj):
        return bool(self.get_payment_open_methods(obj))

    def get_payment_request_open(self, obj):
        return is_payment_open(obj.status, method=None)

    def get_overpaid_amount(self, obj):
        return money_string(order_overpaid(obj))

    def get_debt_override_by_name(self, obj):
        u = obj.debt_override_by
        return u.username if u else None

    def get_deleted_by_name(self, obj):
        u = obj.deleted_by
        return u.username if u else None

    @cached_property
    def _status_request_list_serializer(self):
        return StatusChangeRequestSerializer(many=True, context=self.context)

    @cached_property
    def _payment_list_serializer(self):
        return PaymentSerializer(many=True, context=self.context)

    def get_pending_status_requests(self, obj):
        # Фильтруем по предзагруженному кэшу, без запроса на каждый заказ.
        reqs = [r for r in obj.status_requests.all() if r.status == "pending"]
        return self._status_request_list_serializer.to_representation(reqs)

    def _payments_by_status(self, obj, statuses):
        rows = [p for p in obj.payments.all() if p.status in statuses]
        rows.sort(key=lambda p: p.paid_at)
        return rows

    def get_payments(self, obj):
        # История платежей — только подтверждённые кассой (реально полученные).
        rows = self._payments_by_status(obj, ("confirmed",))
        return self._payment_list_serializer.to_representation(rows)

    def get_pending_payments(self, obj):
        # Оплаты в цепочке подтверждения (запрошена/принята/сверена) видят все
        # сотрудники, которым доступен заказ; клиентам портала — нет.
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or getattr(user, "is_client", False):
            return []
        rows = self._payments_by_status(obj, Payment.IN_PROGRESS_STATUSES)
        return self._payment_list_serializer.to_representation(rows)

    def validate_department(self, code):
        request = self.context.get("request")
        employee = getattr(getattr(request, "user", None), "employee", None)
        assigned = getattr(employee, "sales_department", None)
        if self.instance is None and assigned is not None:
            if not assigned.is_active:
                raise serializers.ValidationError(
                    "Закреплённый отдел продаж отключён — обратитесь к администратору")
            return assigned.code
        if not code:
            if self.instance and self.instance.status not in ("draft", "pending"):
                raise serializers.ValidationError("У подтверждённого заказа должен быть отдел продаж")
            return ""
        qs = Department.objects.filter(code=code)
        if self.instance and self.instance.department == code:
            if qs.exists():
                return code
        if not qs.filter(is_active=True).exists():
            raise serializers.ValidationError("Выберите действующий отдел")
        return code

    def validate(self, attrs):
        if self.instance is None:
            attrs["truck_number"], attrs["trailer_number"] = clean_transport_pair(
                attrs.get("transport_type", "truck"),
                attrs.get("truck_number", ""),
                attrs.get("trailer_number", ""),
            )
        else:
            # Ранняя проверка итоговой пары; под блокировкой её повторяет update().
            _final_transport(self.instance, attrs)
        if "prices" in self.initial_data and not isinstance(self.initial_data["prices"], dict):
            raise serializers.ValidationError({"prices": "Ожидается объект цен по идентификаторам товаров"})
        items = attrs.get("items")
        if items is not None:
            historical_ids = (set(self.instance.items.values_list("product_id", flat=True))
                              if self.instance is not None else set())
            if any(not item["product"].is_active and item["product"].pk not in historical_ids
                   for item in items):
                raise serializers.ValidationError({"items": "Архивный товар нельзя добавлять в заказ"})
            product_ids = [item["product"].pk for item in items]
            if len(product_ids) != len(set(product_ids)):
                raise serializers.ValidationError({"items": "Объедините повторяющиеся товары в одну строку"})
        if self.instance is not None and attrs.get("template_order") is not None:
            raise serializers.ValidationError(
                {
                    "detail": "Шаблон указывается только при создании заказа",
                    "code": "template_on_update",
                }
            )
        if attrs.get("backdate") is not None:
            if self.instance is not None:
                raise serializers.ValidationError(
                    {
                        "detail": "Задним числом создаётся только новый заказ; "
                                  "существующему статус и оплату фиксируют отдельным действием",
                        "code": "backdate_on_update",
                    }
                )
            if not self.initial_data.get("prices"):
                raise serializers.ValidationError(
                    {"backdate": "Для заказа задним числом укажите цены по всем позициям"}
                )
        store = attrs.get("store")
        client = attrs.get("client") or getattr(self.instance, "client", None)
        if self.instance is None and client and client.department_id:
            code = client.department.code
            if attrs.get("department") not in (None, "", code):
                raise serializers.ValidationError(
                    {"department": "Заказ должен учитываться в отделе клиента"}
                )
            attrs["department"] = code
        if store and client and store.client_id != client.id:
            raise serializers.ValidationError(
                {
                    "detail": "Магазин принадлежит другому клиенту",
                    "code": "store_mismatch",
                }
            )
        intent = attrs.get("settlement_intent")
        if self.instance is None and intent is not None:
            attrs["payment_method"] = {
                "pending": "pending",
                "debt": "debt",
                "instant": "invoice",
            }[intent]
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        from apps.warehouse.services import (
            ensure_products_available,
            resolve_warehouse,
        )

        from .services import apply_item_prices, confirm_order

        items = validated_data.pop("items")
        # A reason applies only to a post-shipment correction. Ignore this
        # optional write-only transport field on ordinary order creation.
        validated_data.pop("edit_reason", None)
        template_order = validated_data.pop("template_order", None)
        backdate = validated_data.pop("backdate", None)
        user = self.context["request"].user
        if backdate is not None:
            # Права проверяем до записи: отказ откатывает всю транзакцию.
            assert_can_fixate(user, paid=backdate.get("paid", False))
        warehouse = resolve_warehouse(validated_data.get("warehouse"))
        validated_data["warehouse"] = warehouse
        # Исторический заказ склад не списывает, поэтому и остаток на сегодня
        # для него не важен — товара могло уже не остаться.
        if backdate is None:
            ensure_products_available(
                (item["product"] for item in items),
                warehouse=warehouse,
            )
        validated_data["created_by"] = user
        if validated_data.get("truck_number") or validated_data.get("trailer_number"):
            validated_data["truck_number_set_by"] = user
        validated_data.setdefault("currency", validated_data["client"].currency)

        employee = getattr(user, "employee", None)
        assigned = validated_data["client"].department or getattr(
            employee, "sales_department", None
        )
        if assigned is not None:
            if not assigned.is_active:
                raise serializers.ValidationError(
                    {
                        "department": "Закреплённый отдел продаж отключён — обратитесь к администратору"
                    }
                )
            validated_data["department"] = assigned.code
        else:
            validated_data.setdefault(
                "department",
                validated_data["client"].department.code
                if validated_data["client"].department_id else "",
            )
        if template_order is not None:
            validated_data["repeated_from"] = template_order
        prices_by_product = self.initial_data.get("prices")
        if prices_by_product:
            validated_data["status"] = "pending"
        order = Order.objects.create(**validated_data)
        created = [OrderItem.objects.create(order=order, **item) for item in items]
        if prices_by_product:
            prices_by_item = {
                it.id: prices_by_product.get(str(it.product_id),
                                             prices_by_product.get(it.product_id))
                for it in created
            }
            if user.has_perm_code("orders.confirm") and order.department:
                confirm_order(order, user, prices=prices_by_item)
            else:
                apply_item_prices(order, prices_by_item, user)
            order.refresh_from_db()
        if template_order is not None:
            from apps.eventlog.services import log_event
            log_event(
                "order_repeat",
                f"Создан заказ #{order.pk} по шаблону заказа #{template_order.pk}",
                user=user,
                order=order,
                payload={
                    "source_order_id": template_order.pk,
                    "new_order_id": order.pk,
                    "mode": "reviewed_template",
                },
            )
        if backdate is not None:
            order = fixate_order(order, user, set_created=True, **backdate)
        return order

    @transaction.atomic
    def update(self, instance, validated_data):
        from .services import lock_live_order, replace_items
        user = self.context["request"].user
        edit_reason = validated_data.pop("edit_reason", "")
        # ModelSerializer.save() writes the whole instance. Re-read it under
        # the same parent lock as AI start/finish so a stale PATCH cannot put
        # status/loading_camera back after a physical transition.
        instance = lock_live_order(instance, user)
        # Recheck the final pair against the locked row: another edit may have
        # changed the transport after serializer validation.
        _final_transport(instance, validated_data)
        warehouse_supplied = "warehouse" in validated_data
        requested_warehouse = validated_data.pop("warehouse", None)
        if warehouse_supplied:
            from apps.warehouse.services import (
                ensure_products_available,
                resolve_warehouse,
            )

            current_warehouse = resolve_warehouse(
                instance.warehouse,
                require_active=False,
            )
            requested_warehouse = resolve_warehouse(
                requested_warehouse,
                require_active=False,
            )
            if requested_warehouse.pk != current_warehouse.pk:
                if instance.status in (*AWAITING_SHIPMENT_STATUSES, "shipped"):
                    raise serializers.ValidationError({
                        "detail": "Склад отгрузки нельзя изменить после подтверждения заказа",
                        "code": "warehouse_locked",
                    })
                # A newly selected warehouse must be active. The relaxed
                # resolution above only lets an existing inactive pin remain
                # readable and comparable.
                requested_warehouse = resolve_warehouse(requested_warehouse)
                # Persist before replace_items so its availability check uses the
                # selected warehouse. transaction.atomic rolls this back if a
                # later scalar or item validation fails.
                instance.warehouse = requested_warehouse
                instance.save(update_fields=["warehouse"])
                if "items" not in validated_data:
                    current_items = list(
                        instance.items.select_related("product")
                    )
                    deleted = [
                        item.product_label
                        for item in current_items
                        if item.product_id is None
                    ]
                    if deleted:
                        raise serializers.ValidationError({
                            "detail": (
                                "Нельзя сменить склад: удалены товары — "
                                + ", ".join(deleted)
                            ),
                            "code": "product_deleted",
                        })
                    ensure_products_available(
                        (item.product for item in current_items),
                        warehouse=requested_warehouse,
                    )
            elif instance.warehouse_id is None:
                # Explicitly selecting the effective default pins a legacy-null
                # order without treating it as a business-level warehouse move.
                instance.warehouse = requested_warehouse
                instance.save(update_fields=["warehouse"])
        new_intent = validated_data.get("settlement_intent")
        if new_intent is not None and new_intent != instance.settlement_intent:
            if instance.status == "shipped":
                raise serializers.ValidationError({
                    "detail": "Способ расчёта нельзя изменить после выезда",
                    "code": "settlement_intent_locked",
                })
            validated_data["payment_method"] = {
                "pending": "pending",
                "debt": "debt",
                "instant": "invoice",
            }[new_intent]
        new_client = validated_data.pop("client", None)
        if new_client is not None and new_client.id != instance.client_id:
            raise serializers.ValidationError(
                {
                    "detail": "Клиента изменить нельзя — создайте новый заказ",
                    "code": "client_locked"
                }
            )
        new_currency = validated_data.pop("currency", None)
        if new_currency is not None and new_currency != instance.currency:
            raise serializers.ValidationError(
                {
                    "detail": "Валюту созданного заказа изменить нельзя — создайте новый заказ",
                    "code": "currency_locked"
                }
            )
        new_transport = validated_data.pop("transport_type", None)
        if new_transport is not None and new_transport != instance.transport_type:
            set_transport_type(instance, new_transport, user)
            instance.refresh_from_db()
        if "truck_number" in validated_data or "trailer_number" in validated_data:
            set_order_transport(
                instance,
                user,
                truck=validated_data.pop("truck_number", None),
                trailer=validated_data.pop("trailer_number", None),
            )
            instance.refresh_from_db()
        new_department = validated_data.pop("department", None)
        if new_department is not None and new_department != instance.department:
            set_order_department(instance, new_department, user)
            instance.refresh_from_db()
        items = validated_data.pop("items", None)
        if items is not None:
            replace_items(
                instance,
                items,
                self.initial_data.get("prices"),
                user,
                edit_reason=edit_reason,
            )
            instance.refresh_from_db()
        return super().update(instance, validated_data)


class TransportNumbersSerializer(serializers.Serializer):
    """Номера тягача и прицепа на входе. Непереданный номер не меняется."""

    truck_number = serializers.CharField(max_length=30, required=False, allow_blank=True)
    trailer_number = serializers.CharField(max_length=30, required=False, allow_blank=True)


class ConfirmOrderSerializer(TransportNumbersSerializer):
    """Подтверждение заявки: отдел, цены, «сколько есть» и номер транспорта.

    Цены уходят в сервис как есть — их ошибки и коды задаёт ``_apply_prices``.
    Количество — от 1 мешка; больше запрошенного и чужую позицию отсекает
    сервис. Пустой номер — «не передан»: подтверждение номер не стирает.
    """

    department = serializers.CharField(error_messages={
        key: "Перед подтверждением выберите отдел продаж" for key in ("required", "blank", "null")
    })
    prices = serializers.DictField(required=False)
    quantities = serializers.DictField(child=serializers.IntegerField(min_value=1), required=False)

    def validate(self, attrs):
        for field in ("truck_number", "trailer_number"):
            if not attrs.get(field):
                attrs.pop(field, None)
        return attrs


class TransportSuggestionsMixin:
    """Чипы «как в прошлый раз»: пары клиента, посчитанные один раз на страницу.

    Вьюха кладёт их в context (``transport_pairs``, см.
    ``orders.transport.suggestion_pairs``); без них подсказок нет.
    """

    def get_transport_suggestions(self, order):
        return transport_suggestions(order, self.context.get("transport_pairs") or {})


class OrderTransportRowSerializer(TransportSuggestionsMixin, serializers.ModelSerializer):
    """Строка быстрого ввода «Фуры»: без позиций, оплат и истории заказа."""

    client_name = serializers.SerializerMethodField()
    client_country = serializers.CharField(source="client.country", read_only=True)
    planned_on = serializers.DateField(read_only=True)
    bags = serializers.IntegerField(read_only=True)
    transport_locked = serializers.SerializerMethodField()
    transport_suggestions = serializers.SerializerMethodField()
    plate_warning = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "client",
            "client_name",
            "client_country",
            "status",
            "arrival_date",
            "created_at",
            "planned_on",
            "bags",
            "truck_number",
            "trailer_number",
            "transport_locked",
            "transport_suggestions",
            "plate_warning",
        ]
        read_only_fields = fields

    def get_client_name(self, order):
        return order.client.display_name

    def get_transport_locked(self, order):
        return transport_locked(order, self.context["request"].user)

    def get_plate_warning(self, order):
        return transport_warning(order)
