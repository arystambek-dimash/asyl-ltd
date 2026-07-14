from rest_framework import serializers
from .models import Order, OrderItem, Payment, StatusChangeRequest
from .services import set_truck_number


class OrderItemSerializer(serializers.ModelSerializer):
    product_label = serializers.CharField(source="product.__str__", read_only=True)
    cv_class = serializers.CharField(source="product.cv_class", read_only=True)
    # base_price — справочная цена товара; price — фактическая (договорная при подтверждении).
    base_price = serializers.DecimalField(source="product.price", max_digits=12,
                                          decimal_places=2, read_only=True)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2,
                                          read_only=True, allow_null=True)
    price = serializers.SerializerMethodField()
    client_price = serializers.SerializerMethodField()
    weight_kg = serializers.DecimalField(source="product.weight_kg", max_digits=8,
                                         decimal_places=2, read_only=True)
    # Нужно ли спрашивать вес машины на посту для этого товара.
    ask_truck_weight = serializers.BooleanField(source="product.ask_truck_weight",
                                                read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "product", "product_label", "cv_class", "quantity",
                  "price", "base_price", "unit_price", "client_price", "weight_kg",
                  "ask_truck_weight"]

    def get_price(self, obj):
        # Фактическая цена за мешок: зафиксированная договорная или базовая.
        p = obj.unit_price if obj.unit_price is not None else obj.product.price
        return str(p)

    def get_client_price(self, obj):
        # Подсказка для предзаполнения: текущая цена клиента на этот товар.
        # Нужна только пока цена не зафиксирована; прайс клиента грузим один раз
        # на запрос (кэш в context), а не отдельным запросом на каждую позицию.
        if obj.unit_price is not None:
            return None
        from apps.catalog.models import ClientPrice
        cache = self.context.setdefault("_client_prices", {})
        client_id = obj.order.client_id
        if client_id not in cache:
            cache[client_id] = {
                cp.product_id: str(cp.price)
                for cp in ClientPrice.objects.filter(client_id=client_id)
            }
        return cache[client_id].get(obj.product_id)


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
        return obj.to_status


PAYMENT_METHOD_LABELS = {"cash": "Наличные", "card": "Карта",
                         "kaspi": "Kaspi", "debt": "Долг"}


def _username(user):
    return user.username if user else None


class PaymentSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.SerializerMethodField()
    received_by_name = serializers.SerializerMethodField()
    accountant_by_name = serializers.SerializerMethodField()
    confirmed_by_name = serializers.SerializerMethodField()
    method_label = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = ["id", "order", "amount", "method", "method_label", "status",
                  "paid_at", "recorded_by", "recorded_by_name",
                  "received_by_name", "received_at",
                  "accountant_by_name", "accountant_at",
                  "confirmed_by", "confirmed_by_name", "confirmed_at"]
        read_only_fields = ["order", "paid_at", "recorded_by", "confirmed_by"]

    def get_recorded_by_name(self, obj):
        return _username(obj.recorded_by)

    def get_received_by_name(self, obj):
        return _username(obj.received_by)

    def get_accountant_by_name(self, obj):
        return _username(obj.accountant_by)

    def get_confirmed_by_name(self, obj):
        return _username(obj.confirmed_by)

    def get_method_label(self, obj):
        return PAYMENT_METHOD_LABELS.get(obj.method, obj.method)


class PaymentQueueSerializer(PaymentSerializer):
    """Оплата с контекстом заказа — для табло бухгалтера и кассы."""
    client_name = serializers.CharField(source="order.client.name", read_only=True)
    department = serializers.CharField(source="order.department", read_only=True)
    order_status = serializers.CharField(source="order.status", read_only=True)

    class Meta(PaymentSerializer.Meta):
        fields = PaymentSerializer.Meta.fields + [
            "client_name", "department", "order_status"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)
    status = serializers.CharField(read_only=True)
    payment_status = serializers.CharField(read_only=True)
    settlement_intent = serializers.CharField(required=False)
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

    class Meta:
        model = Order
        fields = ["id", "client", "store", "client_name", "client_phone",
                  "department", "status",
                  "payment_status", "settlement_intent", "transport_type",
                  "truck_number", "arrival_date", "items", "total_amount",
                  "paid_total", "remaining_amount", "is_fully_paid",
                  "is_debt", "debt_override", "debt_override_by_name", "pending_status_requests",
                  "payments", "pending_payments",
                  "weigh_in_kg",
                  "bags_loaded", "bag_estimate_kg", "bag_weight_kg", "created_at",
                  "loading_camera", "deleted_at", "deleted_by_name"]
        read_only_fields = ["debt_override", "department", "deleted_at"]
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
        per = first.product.weight_kg if first else Decimal("0")
        return str(bags * per)

    def get_bag_weight_kg(self, obj):
        from decimal import Decimal
        first = self._first_item(obj)
        per = first.product.weight_kg if first else Decimal("0")
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
        # Заказ можно создать только для клиента, видимого пользователю:
        # менеджер Отдела 2 — исключительно для своих клиентов.
        from apps.rbac.scoping import scope_by_department
        from apps.clients.models import Client
        user = self.context["request"].user
        if not scope_by_department(Client.objects.filter(pk=client.pk), user,
                                   "clients.view").exists():
            raise serializers.ValidationError("Клиент недоступен")
        return client

    def validate(self, attrs):
        store = attrs.get("store")
        client = attrs.get("client") or getattr(self.instance, "client", None)
        if store and client and store.client_id != client.id:
            raise serializers.ValidationError(
                {"detail": "Магазин принадлежит другому клиенту",
                 "code": "store_mismatch"})
        return attrs

    def create(self, validated_data):
        from .services import confirm_order, apply_item_prices
        from apps.warehouse.services import ensure_products_available
        items = validated_data.pop("items")
        # Заказ только на товар в наличии — «нет на складе» отклоняем сразу.
        ensure_products_available(item["product"] for item in items)
        user = self.context["request"].user
        validated_data["created_by"] = user
        # Заказ наследует отдел клиента — данные отделов не смешиваются.
        validated_data["department"] = validated_data["client"].department
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
        return order

    def update(self, instance, validated_data):
        from .services import replace_items
        user = self.context["request"].user
        # Клиент фиксируется при создании: у него свой прайс и отдел.
        new_client = validated_data.pop("client", None)
        if new_client is not None and new_client.id != instance.client_id:
            raise serializers.ValidationError(
                {"detail": "Клиента изменить нельзя — создайте новый заказ",
                 "code": "client_locked"})
        new_truck = validated_data.pop("truck_number", None)
        if new_truck is not None and new_truck != instance.truck_number:
            set_truck_number(instance, new_truck, user)
            instance.refresh_from_db()
        items = validated_data.pop("items", None)
        if items is not None:
            replace_items(instance, items, self.initial_data.get("prices"), user)
            instance.refresh_from_db()
        return super().update(instance, validated_data)
