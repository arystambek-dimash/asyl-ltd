from decimal import Decimal

from rest_framework import serializers

from apps.common.money import CURRENCY_CHOICES, DEFAULT_CURRENCY

from .models import Product
from .photos import product_photo_url


class ProductSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()
    color_label = serializers.CharField(source="get_color_display", read_only=True)
    photo_url = serializers.SerializerMethodField()
    # Коды товара в отчётах о вагонах («Д1с») — словарь бота и «Вставить отчёт».
    aliases = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ["id", "name", "color", "color_label", "weight_kg",
                  "is_active", "label", "photo_url", "aliases"]

    def get_aliases(self, obj):
        # Код — как его пишет отчёт («Д1с»), а не ключ сравнения.
        return [{"id": alias.pk, "code": alias.display_code} for alias in obj.aliases.all()]

    def get_photo_url(self, obj):
        return product_photo_url(obj)

    def _can_view_color(self):
        request = self.context.get("request")
        if request is None:
            return True
        user = request.user
        # Складу цвет и так виден в остатках: без него «Синий» и «Красный»
        # одной фасовки в списке товаров не различить.
        return bool(
            user
            and user.is_authenticated
            and (
                user.has_perm_code("orders.create")
                or user.has_perm_code("warehouse.view")
            )
        )

    def get_label(self, obj):
        if self._can_view_color():
            return str(obj)
        return f"{obj.name} · {obj.packaging_label}"

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self._can_view_color():
            data.pop("color", None)
            data.pop("color_label", None)
        return data


class ClientPriceUpdateItemSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.filter(is_active=True))
    price = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01"),
        required=False, allow_null=True,
    )
    currency = serializers.ChoiceField(
        choices=CURRENCY_CHOICES, required=False, default=DEFAULT_CURRENCY)


class ClientPriceUpdateSerializer(serializers.Serializer):
    prices = ClientPriceUpdateItemSerializer(many=True)

    def validate_prices(self, rows):
        keys = [(row["product"].id, row["currency"]) for row in rows]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError(
                "Товар в одной валюте указан в прайс-листе повторно.")
        return rows
