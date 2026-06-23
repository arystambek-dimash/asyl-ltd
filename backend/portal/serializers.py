from rest_framework import serializers
from catalog.models import Product
from orders.models import Order, OrderItem


class CatalogProductSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="__str__", read_only=True)
    weight_kg = serializers.DecimalField(
        max_digits=10, decimal_places=2, read_only=True
    )

    class Meta:
        model = Product
        fields = ["id", "label", "price", "weight_kg"]


class PortalOrderItemSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(read_only=True)
    product_label = serializers.CharField(source="product.__str__", read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "product", "product_label", "quantity"]


class PortalOrderSerializer(serializers.ModelSerializer):
    items = PortalOrderItemSerializer(many=True)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    paid_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Order
        fields = ["id", "status", "items", "total_amount", "paid_total",
                  "truck_number", "debt_requested", "debt_override", "created_at"]
        read_only_fields = ["status", "truck_number", "debt_requested", "debt_override"]

    def create(self, validated_data):
        items = validated_data.pop("items")
        client = self.context["request"].user.client_profile
        order = Order.objects.create(client=client, status="pending")
        for item in items:
            OrderItem.objects.create(order=order, **item)
        return order
