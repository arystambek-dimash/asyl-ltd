from rest_framework import serializers
from .models import Order, OrderItem, Payment


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["id", "product", "quantity"]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "order", "amount", "paid_at", "recorded_by"]
        read_only_fields = ["order", "paid_at", "recorded_by"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)
    status = serializers.CharField(read_only=True)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    paid_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_fully_paid = serializers.BooleanField(read_only=True)

    class Meta:
        model = Order
        fields = ["id", "client", "status", "truck_number", "items",
                  "total_amount", "paid_total", "is_fully_paid",
                  "debt_override", "created_at"]
        read_only_fields = ["truck_number", "debt_override"]

    def create(self, validated_data):
        items = validated_data.pop("items")
        validated_data["created_by"] = self.context["request"].user
        order = Order.objects.create(**validated_data)
        for item in items:
            OrderItem.objects.create(order=order, **item)
        return order
