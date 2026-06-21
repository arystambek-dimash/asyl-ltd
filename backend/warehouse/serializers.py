from rest_framework import serializers
from .models import StockItem, StockReceipt, StockMovement


class StockItemSerializer(serializers.ModelSerializer):
    product_label = serializers.CharField(source="product.__str__", read_only=True)

    class Meta:
        model = StockItem
        fields = ["id", "product", "product_label", "bags"]


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
