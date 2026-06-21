from rest_framework import serializers
from .models import StockItem, StockReceipt, StockMovement


class StockItemSerializer(serializers.ModelSerializer):
    product_label = serializers.CharField(source="product.__str__", read_only=True)
    grade = serializers.CharField(source="product.grade.name", read_only=True)
    packaging = serializers.CharField(source="product.packaging.name", read_only=True)
    weight_kg = serializers.DecimalField(
        source="product.packaging.weight_kg", max_digits=10, decimal_places=2,
        read_only=True,
    )

    class Meta:
        model = StockItem
        fields = ["id", "product", "product_label", "grade", "packaging",
                  "weight_kg", "bags"]


class StockReceiptSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockReceipt
        fields = ["id", "product", "bags", "received_at", "received_by"]
        read_only_fields = ["received_at", "received_by"]


class StockMovementSerializer(serializers.ModelSerializer):
    product_label = serializers.CharField(source="product.__str__", read_only=True)
    created_by_name = serializers.CharField(
        source="created_by.username", read_only=True, default=None
    )

    class Meta:
        model = StockMovement
        fields = ["id", "product", "product_label", "delta", "balance_after",
                  "reason", "note", "created_at", "created_by_name"]
