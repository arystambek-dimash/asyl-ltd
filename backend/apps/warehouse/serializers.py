from rest_framework import serializers
from .models import StockItem, StockReceipt, StockMovement


class StockAdjustmentSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    delta = serializers.IntegerField()
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class StockItemSerializer(serializers.ModelSerializer):
    product_label = serializers.CharField(source="product.__str__", read_only=True)
    grade = serializers.CharField(source="product.name", read_only=True)
    color = serializers.CharField(source="product.color", read_only=True)
    color_label = serializers.CharField(source="product.get_color_display", read_only=True)
    packaging = serializers.SerializerMethodField()
    weight_kg = serializers.DecimalField(
        source="product.weight_kg", max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = StockItem
        fields = ["id", "product", "product_label", "grade", "color",
                  "color_label", "packaging", "weight_kg", "bags"]

    def get_packaging(self, obj):
        return f"{int(obj.product.weight_kg)} кг"


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
