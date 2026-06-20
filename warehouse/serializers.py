from rest_framework import serializers
from .models import StockItem, StockReceipt


class StockItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockItem
        fields = ["id", "product", "bags"]


class StockReceiptSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockReceipt
        fields = ["id", "product", "bags", "received_at", "received_by"]
        read_only_fields = ["received_at", "received_by"]
