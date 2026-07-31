from rest_framework import serializers
from .models import FactoryMap, StockItem, StockMovement, StockReceipt


FACTORY_MAP_WIDTH = 1200
FACTORY_MAP_HEIGHT = 680


class FactoryZoneSerializer(serializers.Serializer):
    id = serializers.RegexField(r"^[a-zA-Z0-9_-]{1,64}$")
    name = serializers.CharField(max_length=80)
    kind = serializers.ChoiceField(
        choices=[
            "gate",
            "scale",
            "warehouse",
            "silos",
            "production",
            "lab",
            "rail",
            "utility",
        ]
    )
    x = serializers.IntegerField(min_value=0, max_value=FACTORY_MAP_WIDTH)
    y = serializers.IntegerField(min_value=0, max_value=FACTORY_MAP_HEIGHT)
    width = serializers.IntegerField(min_value=48, max_value=FACTORY_MAP_WIDTH)
    height = serializers.IntegerField(min_value=48, max_value=FACTORY_MAP_HEIGHT)
    color = serializers.RegexField(r"^#[0-9A-Fa-f]{6}$")
    note = serializers.CharField(
        max_length=160, required=False, allow_blank=True, default=""
    )

    def validate(self, attrs):
        if attrs["x"] + attrs["width"] > FACTORY_MAP_WIDTH:
            raise serializers.ValidationError("Участок выходит за правую границу схемы")
        if attrs["y"] + attrs["height"] > FACTORY_MAP_HEIGHT:
            raise serializers.ValidationError("Участок выходит за нижнюю границу схемы")
        return attrs


class FactoryMapUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=100)
    zones = FactoryZoneSerializer(many=True)

    def validate_zones(self, zones):
        if len(zones) > 80:
            raise serializers.ValidationError(
                "На схеме может быть не больше 80 участков"
            )
        ids = [zone["id"] for zone in zones]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError(
                "Идентификаторы участков не должны повторяться"
            )
        return zones


class FactoryMapSerializer(serializers.ModelSerializer):
    updated_by_name = serializers.CharField(
        source="updated_by.username", read_only=True, default=None
    )

    class Meta:
        model = FactoryMap
        fields = ["title", "zones", "updated_at", "updated_by_name"]


class StockAdjustmentSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    delta = serializers.IntegerField()
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class StockItemSerializer(serializers.ModelSerializer):
    product_label = serializers.CharField(source="product.__str__", read_only=True)
    grade = serializers.CharField(source="product.name", read_only=True)
    color = serializers.CharField(source="product.color", read_only=True)
    color_label = serializers.CharField(
        source="product.get_color_display", read_only=True
    )
    packaging = serializers.SerializerMethodField()
    weight_kg = serializers.DecimalField(
        source="product.weight_kg", max_digits=10, decimal_places=2, read_only=True
    )

    class Meta:
        model = StockItem
        fields = [
            "id",
            "product",
            "product_label",
            "grade",
            "color",
            "color_label",
            "packaging",
            "weight_kg",
            "bags",
        ]

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
        fields = [
            "id",
            "product",
            "product_label",
            "delta",
            "balance_after",
            "reason",
            "note",
            "created_at",
            "created_by_name",
        ]
