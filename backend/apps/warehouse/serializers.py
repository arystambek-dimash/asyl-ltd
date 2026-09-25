import uuid

from rest_framework import serializers

from apps.catalog.models import Product

from .models import StockItem, Warehouse


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = [
            "id",
            "code",
            "name",
            "address",
            "is_active",
            "is_default",
        ]
        # Операторы только создают и переименовывают склады; основной склад,
        # активность и внутренний код через API не меняются.
        read_only_fields = ["id", "code", "address", "is_active", "is_default"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Укажите название склада")
        # PostgreSQL clusters created with the C locale only fold ASCII in
        # ILIKE/lower(). Python's Unicode casefold keeps the operator-facing
        # validation correct for Cyrillic names too; the DB expression remains
        # the final concurrent-write guard.
        duplicates = Warehouse.objects.all()
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        normalized = value.casefold()
        names = duplicates.values_list("name", flat=True)
        if any(name.strip().casefold() == normalized for name in names):
            raise serializers.ValidationError("Склад с таким названием уже существует")
        return value

    def create(self, validated_data):
        # Stable opaque key that never changes when the display name is edited.
        validated_data["code"] = f"wh-{uuid.uuid4().hex[:12]}"
        return super().create(validated_data)


def _product_field():
    # Архивный товар тоже можно пересчитать или переместить: остатки остаются.
    return serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.all(),
        error_messages={
            "does_not_exist": "Товар не найден",
            "incorrect_type": "Товар не найден",
        },
    )


class StockAdjustmentSerializer(serializers.Serializer):
    warehouse = serializers.PrimaryKeyRelatedField(
        queryset=Warehouse.objects.filter(is_active=True),
        required=False,
    )
    product = _product_field()
    delta = serializers.IntegerField()
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class StockTransferSerializer(serializers.Serializer):
    product = _product_field()
    from_warehouse = serializers.IntegerField()
    to_warehouse = serializers.IntegerField()
    bags = serializers.IntegerField()
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class StockItemSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    product_label = serializers.CharField(source="product.__str__", read_only=True)
    grade = serializers.CharField(source="product.name", read_only=True)
    color = serializers.CharField(source="product.color", read_only=True)
    color_label = serializers.CharField(
        source="product.get_color_display", read_only=True
    )
    packaging = serializers.CharField(
        source="product.packaging_label", read_only=True
    )
    weight_kg = serializers.DecimalField(
        source="product.weight_kg", max_digits=10, decimal_places=2, read_only=True
    )

    class Meta:
        model = StockItem
        fields = [
            "id",
            "warehouse",
            "warehouse_name",
            "warehouse_code",
            "product",
            "product_label",
            "grade",
            "color",
            "color_label",
            "packaging",
            "weight_kg",
            "bags",
        ]
