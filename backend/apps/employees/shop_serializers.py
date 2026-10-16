from decimal import Decimal

from rest_framework import serializers

from apps.catalog.models import Product
from apps.catalog.serializers import PricedProductSerializer
from apps.clients.models import Client
from apps.common.money import money_string
from apps.portal.serializers import MAX_PORTAL_ITEM_QUANTITY, MAX_PORTAL_ORDER_ITEMS

from .models import ShopSale, ShopSaleItem


class ShopCatalogSerializer(PricedProductSerializer):
    stock_bags = serializers.IntegerField(read_only=True)

    class Meta(PricedProductSerializer.Meta):
        fields = [*PricedProductSerializer.Meta.fields, "stock_bags"]


class ShopSaleItemInputSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_PORTAL_ITEM_QUANTITY)
    unit_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01"), required=False,
    )


class ShopSaleCreateSerializer(serializers.Serializer):
    idempotency_key = serializers.UUIDField()
    client = serializers.PrimaryKeyRelatedField(queryset=Client.objects.all(), required=False, allow_null=True)
    items = ShopSaleItemInputSerializer(many=True, allow_empty=False, max_length=MAX_PORTAL_ORDER_ITEMS)


class ShopSaleItemSerializer(serializers.ModelSerializer):
    product_label = serializers.CharField(read_only=True)

    class Meta:
        model = ShopSaleItem
        fields = ["id", "product", "product_label", "quantity", "unit_price"]


class ShopSaleSerializer(serializers.ModelSerializer):
    items = ShopSaleItemSerializer(many=True, read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    client_name = serializers.CharField(source="client.display_name", read_only=True, allow_null=True)
    bags = serializers.IntegerField(read_only=True)
    total_amount = serializers.SerializerMethodField()

    class Meta:
        model = ShopSale
        fields = [
            "id", "created_at", "cancelled_at", "status", "status_label", "currency",
            "client", "client_name", "items", "bags", "total_amount",
        ]

    def get_total_amount(self, obj):
        return money_string(obj.total_amount)
