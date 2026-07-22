from django.db import transaction
from rest_framework import serializers
from apps.clients.models import Department
from .models import Order, OrderItem, Payment, StatusChangeRequest
from .services import set_truck_number
from .statuses import public_status_label


class OrderItemSerializer(serializers.ModelSerializer):
    product_label = serializers.CharField(read_only=True)
    cv_class = serializers.SerializerMethodField()
    # PositiveIntegerField пропускает 0 — заказ из «нулевых» позиций бессмыслен.
    quantity = serializers.IntegerField(min_value=1)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2,
                                          read_only=True, allow_null=True)
    price = serializers.SerializerMethodField()
    client_price = serializers.SerializerMethodField()
    weight_kg = serializers.SerializerMethodField()
    # Нужно ли спрашивать вес машины на посту для этого товара.
    ask_truck_weight = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = ["id", "product", "product_label", "cv_class", "quantity",
                  "price", "unit_price", "client_price", "weight_kg",
                  "ask_truck_weight"]
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
        # До фиксации личной цены у позиции намеренно нет стоимости.
        return str(obj.unit_price) if obj.unit_price is not None else None

    def get_client_price(self, obj):
        # Подсказка для предзаполнения: текущая цена клиента на этот товар.
        # Нужна только пока цена не зафиксирована; прайс клиента грузим один раз
        # на запрос (кэш в context), а не отдельным запросом на каждую позицию.
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
        fields = ["id", "order", "to_status", "to_status_label", "status",
                  "requested_by", "requested_by_name", "decided_by",
                  "created_at", "decided_at"]

    def get_requested_by_name(self, obj):
        return obj.requested_by.username if obj.requested_by else None

    def get_to_status_label(self, obj):
        return public_status_label(obj.to_status)


PAYMENT_METHOD_LABELS = {
    "invoice": "Счет на оплату",
    "kaspi": "Kaspi",
    "cash": "Наличные",
    "debt": "Долг",
    # Легаси-способ внутренних банковских оплат.
    "card": "Карта",
}


def _username(user):
    return user.username if user else None


class PaymentSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.SerializerMethodField()
    received_by_name = serializers.SerializerMethodField()
    confirmed_by_name = serializers.SerializerMethodField()
    method_label = serializers.SerializerMethodField()
    currency = serializers.CharField(source="order.currency", read_only=True)

    class Meta:
        model = Payment
        fields = ["id", "order", "currency", "amount", "method", "method_label", "status",
                  "note", "paid_at", "recorded_by", "recorded_by_name",
                  "received_by_name", "received_at",
                  "confirmed_by", "confirmed_by_name", "confirmed_at"]
        read_only_fields = ["order", "paid_at", "recorded_by", "confirmed_by"]

    def get_recorded_by_name(self, obj):
        return _username(obj.recorded_by)

    def get_received_by_name(self, obj):
        return _username(obj.received_by)

    def get_confirmed_by_name(self, obj):
        return _username(obj.confirmed_by)

    def get_method_label(self, obj):
        return PAYMENT_METHOD_LABELS.get(obj.method, obj.method)


class DepartmentLabelMixin:
    """department_name/department_color по коду отдела — один справочник на
    сериализацию списка (кэш на инстансе), без запроса на каждую строку."""

    def _department_code(self, obj):
        return obj.department

    def _department(self, code):
        if not hasattr(self, "_departments"):
            self._departments = {row.code: row for row in Department.objects.all()}
        return self._departments.get(code)

    def get_department_name(self, obj):
        code = self._department_code(obj)
        row = self._department(code)
        return row.name if row else code

    def get_department_color(self, obj):
        row = self._department(self._department_code(obj))
        return row.color if row else "#64748B"


class PaymentQueueSerializer(DepartmentLabelMixin, PaymentSerializer):
    """Оплата с контекстом заказа — для табло бухгалтера и кассы."""
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


class OrderSerializer(DepartmentLabelMixin, serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)
    status = serializers.CharField(read_only=True)
    payment_status = serializers.CharField(read_only=True)
    settlement_intent = serializers.CharField(required=False)
    payment_method = serializers.CharField(read_only=True)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    paid_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    remaining_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_fully_paid = serializers.BooleanField(read_only=True)
    is_debt = serializers.BooleanField(read_only=True)
    client_name = serializers.CharField(source="client.name", read_only=True)
    client_phone = serializers.CharField(source="client.phone", read_only=True)
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
    department = serializers.CharField(required=False)
    department_name = serializers.SerializerMethodField()
    department_color = serializers.SerializerMethodField()
    currency = serializers.ChoiceField(choices=Order.CURRENCIES, required=False)
    # Источник шаблона передаётся только при создании. Сам заказ всё равно
    # создаётся обычной формой после ручной проверки менеджером.
    template_order = serializers.PrimaryKeyRelatedField(
        queryset=Order.objects.all(), write_only=True, required=False,
    )

    class Meta:
        model = Order
        fields = ["id", "client", "store", "client_name", "client_phone",
                  "department", "department_name", "department_color", "status",
                  "currency",
                  "payment_status", "settlement_intent", "payment_method", "transport_type",
                  "truck_number", "arrival_date", "notes", "items", "total_amount",
                  "paid_total", "remaining_amount", "is_fully_paid",
                  "is_debt", "debt_override", "debt_override_by_name", "pending_status_requests",
                  "payments", "pending_payments",
                  "weigh_in_kg",
                  "bags_loaded", "bag_estimate_kg", "bag_weight_kg", "created_at",
                  "shipped_at", "loading_camera", "repeated_from",
                  "template_order",
                  "deleted_at", "deleted_by_name"]
        read_only_fields = ["debt_override", "repeated_from", "deleted_at"]
        extra_kwargs = {
            "truck_number": {"required": False},
            "arrival_date": {"required": False, "allow_null": True},
            "store": {"required": False, "allow_null": True},
            "transport_type": {"required": False},
        }

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
        # Ожидаемый вес по ФАКТУ камеры = посчитанные мешки × вес фасовки.
        from decimal import Decimal
        s = self._shipment(obj)
        bags = s.bags_loaded if s else 0
        first = self._first_item(obj)
        per = first.product_weight_kg if first else Decimal("0")
        return str(bags * per)

    def get_bag_weight_kg(self, obj):
        from decimal import Decimal
        first = self._first_item(obj)
        per = first.product_weight_kg if first else Decimal("0")
        return str(per)

    def get_debt_override_by_name(self, obj):
        u = obj.debt_override_by
        return u.username if u else None

    def get_deleted_by_name(self, obj):
        u = obj.deleted_by
        return u.username if u else None

    def get_pending_status_requests(self, obj):
        # Фильтруем по предзагруженному кэшу, без запроса на каждый заказ.
        reqs = [r for r in obj.status_requests.all() if r.status == "pending"]
        return StatusChangeRequestSerializer(reqs, many=True).data

    def _payments_by_status(self, obj, statuses):
        rows = [p for p in obj.payments.all() if p.status in statuses]
        rows.sort(key=lambda p: p.paid_at)
        return rows

    def get_payments(self, obj):
        # История платежей — только подтверждённые кассой (реально полученные).
        rows = self._payments_by_status(obj, ("confirmed",))
        return PaymentSerializer(rows, many=True).data

    def get_pending_payments(self, obj):
        # Оплаты в цепочке подтверждения (запрошена/принята/сверена) видят все
        # сотрудники, которым доступен заказ; клиентам портала — нет.
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or getattr(user, "is_client", False):
            return []
        rows = self._payments_by_status(obj, Payment.IN_PROGRESS_STATUSES)
        return PaymentSerializer(rows, many=True).data

    def validate_client(self, client):
        # Заказ можно создать только для клиента, доступного пользователю.
        from apps.clients.querysets import visible_clients
        user = self.context["request"].user
        if not visible_clients(user).filter(pk=client.pk).exists():
            raise serializers.ValidationError("Клиент недоступен")
        return client

    def validate_department(self, code):
        request = self.context.get("request")
        employee = getattr(getattr(request, "user", None), "employee", None)
        assigned = getattr(employee, "sales_department", None)
        if self.instance is None and assigned is not None:
            if not assigned.is_active:
                raise serializers.ValidationError(
                    "Закреплённый отдел продаж отключён — обратитесь к администратору")
            return assigned.code
        qs = Department.objects.filter(code=code)
        if self.instance and self.instance.department == code:
            if qs.exists():
                return code
        if not qs.filter(is_active=True).exists():
            raise serializers.ValidationError("Выберите действующий отдел")
        return code

    def validate(self, attrs):
        if self.instance is not None and attrs.get("template_order") is not None:
            raise serializers.ValidationError({
                "detail": "Шаблон указывается только при создании заказа",
                "code": "template_on_update",
            })
        store = attrs.get("store")
        client = attrs.get("client") or getattr(self.instance, "client", None)
        if store and client and store.client_id != client.id:
            raise serializers.ValidationError(
                {"detail": "Магазин принадлежит другому клиенту",
                 "code": "store_mismatch"})
        intent = attrs.get("settlement_intent")
        if intent in Order.SETTLEMENT_INTENTS:
            attrs["payment_method"] = "debt" if intent == "debt" else "invoice"
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        # Атомарно: упавшее подтверждение (например, price_required) не должно
        # оставлять в базе заказ-сироту без цен.
        from .services import confirm_order, apply_item_prices
        from apps.warehouse.services import ensure_products_available
        items = validated_data.pop("items")
        template_order = validated_data.pop("template_order", None)
        # Заказ только на товар в наличии — «нет на складе» отклоняем сразу.
        ensure_products_available(item["product"] for item in items)
        user = self.context["request"].user
        validated_data["created_by"] = user
        validated_data.setdefault("currency", validated_data["client"].currency)
        # Для сотрудника отдела продаж отдел нельзя подменить на клиенте:
        # сервер всегда закрепляет его назначение. Остальным оставляем выбор.
        employee = getattr(user, "employee", None)
        assigned = getattr(employee, "sales_department", None)
        if assigned is not None:
            if not assigned.is_active:
                raise serializers.ValidationError({
                    "department": "Закреплённый отдел продаж отключён — обратитесь к администратору"
                })
            validated_data["department"] = assigned.code
        else:
            # Для старых API-клиентов используем основной отдел.
            validated_data.setdefault("department", Department.default_code())
        if template_order is not None:
            validated_data["repeated_from"] = template_order
        # Оператор (orders.confirm) создаёт заказ сразу подтверждённым с ценами;
        # заявка менеджера Отдела 2 остаётся pending до подтверждения бухгалтером.
        # prices приходит по товару: {product_id: цена} (у позиций ещё нет id).
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
            if user.has_perm_code("orders.confirm"):
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
        return order

    def update(self, instance, validated_data):
        from .services import replace_items
        user = self.context["request"].user
        # Клиент фиксируется при создании: у него свой прайс-лист.
        new_client = validated_data.pop("client", None)
        if new_client is not None and new_client.id != instance.client_id:
            raise serializers.ValidationError(
                {"detail": "Клиента изменить нельзя — создайте новый заказ",
                 "code": "client_locked"})
        new_currency = validated_data.pop("currency", None)
        if new_currency is not None and new_currency != instance.currency:
            raise serializers.ValidationError(
                {"detail": "Валюту созданного заказа изменить нельзя — создайте новый заказ",
                 "code": "currency_locked"})
        new_truck = validated_data.pop("truck_number", None)
        if new_truck is not None and new_truck != instance.truck_number:
            set_truck_number(instance, new_truck, user)
            instance.refresh_from_db()
        items = validated_data.pop("items", None)
        if items is not None:
            replace_items(instance, items, self.initial_data.get("prices"), user)
            instance.refresh_from_db()
        return super().update(instance, validated_data)
