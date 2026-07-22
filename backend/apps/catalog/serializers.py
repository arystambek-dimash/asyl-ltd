from decimal import Decimal

from rest_framework import serializers
from .models import ClientPrice, Product


class ProductSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()
    color_label = serializers.CharField(source="get_color_display", read_only=True)
    cv_class = serializers.CharField(read_only=True)
    available_bags = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ["id", "name", "color", "color_label", "weight_kg",
                  "is_active", "ask_truck_weight",
                  "label", "cv_class", "available_bags"]

    def get_available_bags(self, obj):
        # Остаток склада: заказ доступен только по товару в наличии.
        stock = getattr(obj, "stock", None)
        return stock.bags if stock else 0

    def _can_view_color(self):
        request = self.context.get("request")
        if request is None:
            return True
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (user.is_superuser or user.has_perm_code("orders.create"))
        )

    def get_label(self, obj):
        if self._can_view_color():
            return str(obj)
        return f"{obj.name} · {int(obj.weight_kg)} кг"

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self._can_view_color():
            # Цвет — рабочая классификация для сотрудников, создающих заказы.
            # У остальных он не должен утекать ни отдельным полем, ни CV-классом.
            data.pop("color", None)
            data.pop("color_label", None)
            data.pop("cv_class", None)
        return data


class ClientPriceUpdateItemSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.filter(is_active=True))
    price = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01"),
        required=False, allow_null=True,
    )
    currency = serializers.ChoiceField(
        choices=ClientPrice.CURRENCIES, required=False, default="KZT")


class ClientPriceUpdateSerializer(serializers.Serializer):
    prices = ClientPriceUpdateItemSerializer(many=True)

    def validate_prices(self, rows):
        keys = [(row["product"].id, row["currency"]) for row in rows]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError(
                "Товар в одной валюте указан в прайс-листе повторно.")
        return rows
