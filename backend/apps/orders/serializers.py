from rest_framework import serializers
from .models import Order, OrderItem, Payment
from .services import set_truck_number


class OrderItemSerializer(serializers.ModelSerializer):
    product_label = serializers.CharField(source="product.__str__", read_only=True)
    cv_class = serializers.CharField(source="product.cv_class", read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "product", "product_label", "cv_class", "quantity"]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "order", "amount", "method", "status",
                  "paid_at", "recorded_by", "confirmed_by"]
        read_only_fields = ["order", "paid_at", "recorded_by", "confirmed_by"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)
    status = serializers.CharField(read_only=True)
    payment_status = serializers.CharField(read_only=True)
    settlement_intent = serializers.CharField(required=False)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    paid_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    remaining_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_fully_paid = serializers.BooleanField(read_only=True)
    client_name = serializers.CharField(source="client.name", read_only=True)
    client_phone = serializers.CharField(source="client.phone", read_only=True)
    weigh_in_kg = serializers.SerializerMethodField()
    bags_loaded = serializers.SerializerMethodField()
    bag_estimate_kg = serializers.SerializerMethodField()
    bag_weight_kg = serializers.SerializerMethodField()
    debt_override_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "client", "store", "client_name", "client_phone", "status",
                  "payment_status", "settlement_intent",
                  "truck_number", "arrival_date", "items", "total_amount",
                  "paid_total", "remaining_amount", "is_fully_paid",
                  "debt_override", "debt_override_by_name",
                  "weigh_in_kg",
                  "bags_loaded", "bag_estimate_kg", "bag_weight_kg", "created_at"]
        read_only_fields = ["debt_override"]
        extra_kwargs = {
            "truck_number": {"required": False},
            "arrival_date": {"required": False, "allow_null": True},
            "store": {"required": False, "allow_null": True},
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

    def create(self, validated_data):
        items = validated_data.pop("items")
        validated_data["created_by"] = self.context["request"].user
        order = Order.objects.create(**validated_data)
        for item in items:
            OrderItem.objects.create(order=order, **item)
        return order

    def update(self, instance, validated_data):
        new_truck = validated_data.pop("truck_number", None)
        user = self.context["request"].user
        if new_truck is not None and new_truck != instance.truck_number:
            set_truck_number(instance, new_truck, user)
            instance.refresh_from_db()
        return super().update(instance, validated_data)
