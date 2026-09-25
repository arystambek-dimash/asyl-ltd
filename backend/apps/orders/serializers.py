from django.db import transaction
from django.utils.functional import cached_property
from rest_framework import serializers

from apps.clients.models import Client, Store
from apps.common.money import CURRENCY_CHOICES, money_string
from apps.sales.access import assigned_department_id, scope_by_client_department
from apps.sales.labels import department_label
from apps.sales.models import Department
from apps.shipments.services import estimated_load_kg

from .debt import order_overpaid
from .fixation import OrderFixationSerializer
from .labels import (
    order_payment_method_label,
    payment_method_label,
    payment_stage_label,
    payment_status_label,
)
from .models import Order, OrderItem, Payment, StatusChangeRequest
from .services import (
    create_staff_order,
    lock_live_order,
    order_items_error,
    reopen_confirmed_payment_error,
    replace_items,
    restore_rejected_payment_error,
    set_order_department,
    set_order_warehouse,
    set_transport_type,
)
from .statuses import REVIEWABLE_STATUSES, is_payment_method_allowed, is_payment_open
from .transport import (
    clean_transport_pair,
    order_wagons,
    set_order_transport,
    transport_suggestions,
)


class OrderItemSerializer(serializers.ModelSerializer):
    product_label = serializers.CharField(read_only=True)
    quantity = serializers.IntegerField(min_value=1, max_value=2_147_483_647)
    unit_price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
        allow_null=True
    )
    client_price = serializers.SerializerMethodField()
    weight_kg = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product",
            "product_label",
            "quantity",
            "unit_price",
            "client_price",
            "weight_kg",
        ]
        extra_kwargs = {
            "product": {"required": True, "allow_null": False},
        }

    def get_weight_kg(self, obj):
        return str(obj.product_weight_kg)

    def get_client_price(self, obj):
        if obj.unit_price is not None:
            return None
        if obj.product_id is None:
            return None
        cache = self.context.setdefault("_client_prices", {})
        client = obj.order.client
        cache_key = (client.id, obj.order.currency)
        if cache_key not in cache:
            cache[cache_key] = {
                cp.product_id: str(cp.price)
                for cp in client.prices.all() if cp.currency == obj.order.currency
            }
        return cache[cache_key].get(obj.product_id)


def _username_field(relation: str) -> serializers.CharField:
    """Логин автора по связи; пустая связь — None."""
    return serializers.CharField(source=f"{relation}.username", default=None, read_only=True)


def _invoice(payment):
    """Счёт ApiPay оплаты или None (у кассовой оплаты его нет)."""
    return getattr(payment, "apipay_invoice", None)


def apipay_invoice_data(invoice):
    """Счёт ApiPay в ответе API — один вид для кассы и кабинета клиента."""
    if invoice is None:
        return None
    return {
        "invoice_id": invoice.invoice_id,
        "channel": invoice.channel,
        "status": invoice.status,
        "phone_number": invoice.phone_number or None,
        "qr_token_url": invoice.qr_token_url or None,
        "qr_image_url": invoice.qr_image_url or None,
        "qr_expires_at": invoice.qr_expires_at,
    }


class StatusChangeRequestSerializer(serializers.ModelSerializer):
    requested_by_name = _username_field("requested_by")

    class Meta:
        model = StatusChangeRequest
        fields = [
            "id",
            "order",
            "to_status",
            "status",
            "requested_by_name",
            "created_at",
            "decided_at"
        ]


class PaymentSerializer(serializers.ModelSerializer):
    recorded_by_name = _username_field("recorded_by")
    received_by_name = _username_field("received_by")
    confirmed_by_name = _username_field("confirmed_by")
    method_label = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()
    currency = serializers.CharField(source="order.currency", read_only=True)
    provider = serializers.SerializerMethodField()
    client_name = serializers.CharField(source="order.client.name", read_only=True)
    effective_status = serializers.SerializerMethodField()
    effective_status_label = serializers.SerializerMethodField()
    available_for_refund = serializers.SerializerMethodField()
    refunds = serializers.SerializerMethodField()
    can_restore = serializers.SerializerMethodField()
    can_reopen = serializers.SerializerMethodField()
    can_issue = serializers.SerializerMethodField()
    confirmation_mode = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = ["id", "order", "currency", "amount", "method", "method_label", "status", "status_label",
                  "note", "paid_at", "recorded_by_name",
                  "received_by_name", "received_at",
                  "confirmed_by_name", "confirmed_at",
                  "client_name", "provider", "effective_status", "effective_status_label",
                  "refunded_amount", "pending_refund_amount",
                  "available_for_refund", "refunds",
                  "can_restore", "can_reopen", "can_issue", "confirmation_mode"]
        read_only_fields = ["order", "paid_at"]

    def get_method_label(self, obj):
        return payment_method_label(obj.method)

    def get_status_label(self, obj):
        return payment_status_label(obj.status)

    def get_effective_status(self, obj):
        if obj.status in Payment.IN_PROGRESS_STATUSES:
            provider = _invoice(obj)
            if provider is None:
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

    def get_effective_status_label(self, obj):
        return payment_stage_label(self.get_effective_status(obj))

    def get_available_for_refund(self, obj):
        return money_string(obj.available_for_refund)

    def _request_can(self, code):
        # Без запроса права не известны — действия не предлагаем (как
        # OrderSerializer.get_pending_payments): иначе флаги ушли бы «как админу».
        request = self.context.get("request")
        return request is not None and request.user.has_perm_code(code)

    def get_can_reopen(self, obj):
        """Ошибочно подтверждённую оплату кассы можно вернуть на проверку."""
        return (
            self._request_can("payments.confirm")
            and reopen_confirmed_payment_error(obj) is None
        )

    def get_can_restore(self, obj):
        return (
            self._request_can("payments.confirm")
            and restore_rejected_payment_error(obj) is None
        )

    def get_can_issue(self, obj):
        if (
                not self._request_can("payments.create")
                or obj.status not in Payment.IN_PROGRESS_STATUSES
                or obj.method != "invoice"
        ):
            return False
        invoice = _invoice(obj)
        if invoice is None:
            return True
        return invoice.invoice_id is None and not (
            invoice.channel == "qr" and invoice.status == "creating"
        )

    def get_confirmation_mode(self, obj):
        return "automatic" if _invoice(obj) else "manual"

    def get_refunds(self, obj):
        return [
            {
                "id": row.pk,
                "amount": money_string(row.amount),
                "method": row.method,
                "status": row.status,
                "reason": row.reason,
                "requested_by_name": row.requested_by.username if row.requested_by else None,
                "completed_at": row.completed_at,
                "created_at": row.created_at,
            }
            for row in obj.payment_refunds.all()
        ]

    def get_provider(self, obj):
        return apipay_invoice_data(_invoice(obj))


class DepartmentLabelMixin:
    def _department_code(self, obj):
        return obj.department

    def _department(self, code):
        if not hasattr(self, "_departments"):
            self._departments = {row.code: row for row in Department.objects.all()}
        return self._departments.get(code)

    def get_department_name(self, obj):
        code = self._department_code(obj)
        return department_label(code, self._department(code))[0]

    def get_department_color(self, obj):
        code = self._department_code(obj)
        return department_label(code, self._department(code))[1]


class PaymentQueueSerializer(DepartmentLabelMixin, PaymentSerializer):
    department = serializers.CharField(source="order.department", read_only=True)
    department_name = serializers.SerializerMethodField()
    department_color = serializers.SerializerMethodField()
    store_name = serializers.CharField(source="order.store.name", read_only=True,
                                       allow_null=True)

    class Meta(PaymentSerializer.Meta):
        fields = PaymentSerializer.Meta.fields + [
            "department", "department_name", "department_color", "store_name"]

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
    # Способ расчёта меняют только сервисы оплаты и долга (журнал, проверки).
    settlement_intent = serializers.CharField(read_only=True)
    payment_method = serializers.CharField(read_only=True)
    payment_method_label = serializers.SerializerMethodField()
    total_amount = serializers.DecimalField(max_digits=30, decimal_places=2, read_only=True)
    paid_total = serializers.DecimalField(max_digits=30, decimal_places=2, read_only=True)
    remaining_amount = serializers.DecimalField(max_digits=30, decimal_places=2, read_only=True)
    # Окно оплаты для сотрудника считает сервер (statuses.is_payment_open и
    # is_payment_method_allowed): фронт не повторяет правила статусов и валюты
    # и показывает только открытые способы.
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
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    weigh_in_kg = serializers.SerializerMethodField()
    bags_loaded = serializers.SerializerMethodField()
    bag_estimate_kg = serializers.SerializerMethodField()
    deleted_by_name = _username_field("deleted_by")
    pending_status_requests = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()
    pending_payments = serializers.SerializerMethodField()
    shipped_at = serializers.SerializerMethodField()
    wagons = serializers.SerializerMethodField()
    department = serializers.CharField(required=False, allow_blank=True)
    department_name = serializers.SerializerMethodField()
    department_color = serializers.SerializerMethodField()
    currency = serializers.ChoiceField(choices=CURRENCY_CHOICES, required=False)
    # Источник шаблона передаётся только при создании. Сам заказ всё равно
    # создаётся обычной формой после ручной проверки менеджером.
    template_order = serializers.PrimaryKeyRelatedField(
        queryset=Order.objects.all(), write_only=True, required=False,
    )
    # Заказ задним числом: дата, статус и оплата проставляются одной
    # транзакцией с созданием (см. orders/fixation.py).
    backdate = OrderFixationSerializer(write_only=True, required=False)
    # Цены по id товара: ошибки значений задаёт apply_item_prices.
    prices = serializers.DictField(
        write_only=True,
        required=False,
        error_messages={"not_a_dict": "Ожидается объект цен по идентификаторам товаров"},
    )

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
            "rejection_reason",
            "currency",
            "payment_status",
            "settlement_intent",
            "payment_method",
            "payment_method_label",
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
            "pending_status_requests",
            "payments",
            "pending_payments",
            "weigh_in_kg",
            "bags_loaded",
            "bag_estimate_kg",
            "created_at",
            "shipped_at",
            "loading_camera",
            "repeated_from",
            "template_order",
            "backdate",
            "prices",
            "edit_reason",
            "deleted_at",
            "deleted_by_name",
        ]
        read_only_fields = [
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

    def get_bag_estimate_kg(self, obj):
        """Ожидаемый вес груза по факту камеры.

        Считаем по всем позициям: у смешанного заказа фасовки разные, и вес
        первой позиции, умноженный на все мешки, завышал число (30×50кг +
        20×25кг давало 2500 вместо 2000). Расчёт должен совпадать с итогом
        поста погрузки и не зависит от физического сервиса весов Grain.
        """
        ordered = obj.ordered_bags
        if not ordered:
            return money_string(0)
        shipment = self._shipment(obj)
        bags = shipment.bags_loaded if shipment else 0
        # Камера насчитала не столько, сколько заказано: состав недогруза
        # неизвестен, поэтому масштабируем средним весом мешка по заказу.
        return money_string(estimated_load_kg(obj) * bags / ordered)

    def get_payment_method_label(self, obj):
        return order_payment_method_label(obj.payment_method)

    def get_payment_open_methods(self, obj):
        return [
            method for method in Payment.CASHIER_METHODS
            if is_payment_open(obj.status, method=method)
            and is_payment_method_allowed(obj.currency, method)
        ]

    def get_payment_open(self, obj):
        return bool(self.get_payment_open_methods(obj))

    def get_payment_request_open(self, obj):
        return is_payment_open(obj.status, method=None) and is_payment_method_allowed(
            obj.currency, None
        )

    def get_overpaid_amount(self, obj):
        return money_string(order_overpaid(obj))

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
        if not code:
            if self.instance and self.instance.status not in REVIEWABLE_STATUSES:
                raise serializers.ValidationError("У подтверждённого заказа должен быть отдел продаж")
            return ""
        qs = Department.objects.filter(code=code)
        if self.instance and self.instance.department == code:
            if qs.exists():
                return code
        if not qs.filter(is_active=True).exists():
            raise serializers.ValidationError("Выберите действующий отдел")
        return code

    def _new_order_department(self, client, requested: str) -> str:
        """Отдел нового заказа: отдел клиента, иначе отдел сотрудника, иначе из формы.

        Выбор в форме у сотрудника отдела не учитывается (как у клиентов):
        его область — только свой отдел. Суперюзер — без отдела (sales.access).
        """
        department_id = assigned_department_id(self.context["request"].user)
        if client.department_id is not None:
            department = client.department
            if department_id is None and requested not in ("", department.code):
                raise serializers.ValidationError(
                    {"department": "Заказ должен учитываться в отделе клиента"}
                )
        elif department_id is not None:
            department = Department.objects.get(pk=department_id)
        else:
            return requested
        if not department.is_active:
            raise serializers.ValidationError(
                {"department": "Закреплённый отдел продаж отключён — обратитесь к администратору"}
            )
        return department.code

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
        items = attrs.get("items")
        if items is not None:
            historical_ids = (set(self.instance.items.values_list("product_id", flat=True))
                              if self.instance is not None else set())
            if error := order_items_error(items, historical_ids):
                raise serializers.ValidationError({"items": error})
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
            if not attrs.get("prices"):
                raise serializers.ValidationError(
                    {"backdate": "Для заказа задним числом укажите цены по всем позициям"}
                )
        store = attrs.get("store")
        client = attrs.get("client") or getattr(self.instance, "client", None)
        if self.instance is None:
            attrs["department"] = self._new_order_department(client, attrs.get("department", ""))
        if store and client and store.client_id != client.id:
            raise serializers.ValidationError(
                {
                    "detail": "Магазин принадлежит другому клиенту",
                    "code": "store_mismatch",
                }
            )
        return attrs

    def create(self, validated_data):
        # A reason applies only to a post-shipment correction. Ignore this
        # optional write-only transport field on ordinary order creation.
        validated_data.pop("edit_reason", None)
        items = validated_data.pop("items")
        return create_staff_order(
            self.context["request"].user,
            validated_data,
            items,
            prices=validated_data.pop("prices", None),
            template_order=validated_data.pop("template_order", None),
            backdate=validated_data.pop("backdate", None),
        )

    @transaction.atomic
    def update(self, instance, validated_data):
        user = self.context["request"].user
        edit_reason = validated_data.pop("edit_reason", "")
        prices = validated_data.pop("prices", None)
        # ModelSerializer.save() writes the whole instance. Re-read it under
        # the same parent lock as AI start/finish so a stale PATCH cannot put
        # status/loading_camera back after a physical transition.
        instance = lock_live_order(instance, user)
        # Recheck the final pair against the locked row: another edit may have
        # changed the transport after serializer validation.
        _final_transport(instance, validated_data)
        if "warehouse" in validated_data:
            # До replace_items: его проверка остатка идёт уже по новому складу.
            set_order_warehouse(
                instance,
                validated_data.pop("warehouse"),
                user,
                check_items="items" not in validated_data,
            )
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
                prices,
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

    Цены уходят в сервис как есть — их ошибки и коды задаёт ``apply_item_prices``.
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
