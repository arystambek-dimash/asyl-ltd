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

    class Meta:
        model = OrderItem
        fields = ["id", "product", "product_label", "cv_class", "quantity",
                  "price", "base_price", "unit_price", "client_price", "weight_kg"]

    def get_price(self, obj):
        # Фактическая цена за мешок: зафиксированная договорная или базовая.
        p = obj.unit_price if obj.unit_price is not None else obj.product.price
        return str(p)

    def get_client_price(self, obj):
        # Подсказка для предзаполнения: текущая цена клиента на этот товар, если есть.
        from apps.catalog.models import ClientPrice
        cp = ClientPrice.objects.filter(
            client=obj.order.client, product=obj.product).first()
        return str(cp.price) if cp else None


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


class PaymentSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.SerializerMethodField()
    method_label = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = ["id", "order", "amount", "method", "method_label", "status",
                  "paid_at", "recorded_by", "recorded_by_name", "confirmed_by"]
        read_only_fields = ["order", "paid_at", "recorded_by", "confirmed_by"]

    def get_recorded_by_name(self, obj):
        return obj.recorded_by.username if obj.recorded_by else None

    def get_method_label(self, obj):
        return PAYMENT_METHOD_LABELS.get(obj.method, obj.method)


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
    pending_status_requests = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "client", "store", "client_name", "client_phone", "status",
                  "payment_status", "settlement_intent", "transport_type",
                  "truck_number", "arrival_date", "items", "total_amount",
                  "paid_total", "remaining_amount", "is_fully_paid",
                  "is_debt", "debt_override", "debt_override_by_name", "pending_status_requests",
                  "payments",
                  "weigh_in_kg",
                  "bags_loaded", "bag_estimate_kg", "bag_weight_kg", "created_at"]
        read_only_fields = ["debt_override"]
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

    def get_bag_estimate_kg(self, obj):
        # Ожидаемый вес по ФАКТУ камеры = посчитанные мешки × вес фасовки.
        from decimal import Decimal
        s = self._shipment(obj)
        bags = s.bags_loaded if s else 0
        per = obj.items.first().product.weight_kg if obj.items.exists() else Decimal("0")
        return str(bags * per)

    def get_bag_weight_kg(self, obj):
        from decimal import Decimal
        per = obj.items.first().product.weight_kg if obj.items.exists() else Decimal("0")
        return str(per)

    def get_debt_override_by_name(self, obj):
        u = obj.debt_override_by
        return u.username if u else None

    def get_pending_status_requests(self, obj):
        qs = obj.status_requests.filter(status="pending")
        return StatusChangeRequestSerializer(qs, many=True).data

    def get_payments(self, obj):
        # История платежей — только подтверждённые (реально полученные деньги).
        # Неподтверждённые заявки клиента (pending) не показываем как «получено».
        qs = obj.payments.filter(status="confirmed").order_by("paid_at")
        return PaymentSerializer(qs, many=True).data

    def create(self, validated_data):
        from .services import confirm_order
        items = validated_data.pop("items")
        user = self.context["request"].user
        validated_data["created_by"] = user
        # Оператор создаёт заказ сразу подтверждённым с ценами.
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
            confirm_order(order, user, prices=prices_by_item)
            order.refresh_from_db()
        return order

    def update(self, instance, validated_data):
        new_truck = validated_data.pop("truck_number", None)
        user = self.context["request"].user
        if new_truck is not None and new_truck != instance.truck_number:
            set_truck_number(instance, new_truck, user)
            instance.refresh_from_db()
        return super().update(instance, validated_data)
