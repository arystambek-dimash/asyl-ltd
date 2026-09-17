from decimal import Decimal

from rest_framework import serializers

from apps.common.money import money_string

from .models import Shipment, WaybillSettings


class ArrivalSerializer(serializers.Serializer):
    # Если вес не передан, backend сохраняет расчётный вес заказа.
    weigh_in_kg = serializers.DecimalField(max_digits=12, decimal_places=2,
                                           required=False, allow_null=True)


class LoadSerializer(serializers.Serializer):
    bags = serializers.IntegerField(min_value=0)


class ShipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shipment
        fields = [
            "id",
            "order",
            "truck_number",
            "weigh_in_kg",
            "bags_loaded",
            "arrived_at",
            "shipped_at",
        ]


class LoaderOrderItemSerializer(serializers.Serializer):
    label = serializers.CharField(source="product_label")
    quantity = serializers.IntegerField()
    weight_kg = serializers.DecimalField(source="product_weight_kg", max_digits=10, decimal_places=2)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)


class LoaderOrderSerializer(serializers.Serializer):
    """Заказ на экране грузчика: кому, на чём, что и сколько — без истории поста."""

    id = serializers.IntegerField()
    status = serializers.CharField()
    transport_type = serializers.CharField()
    truck_number = serializers.CharField()
    currency = serializers.CharField()
    arrival_date = serializers.DateField(allow_null=True)
    created_at = serializers.DateTimeField()
    client_name = serializers.SerializerMethodField()
    items = LoaderOrderItemSerializer(many=True, source="items.all")
    bags = serializers.SerializerMethodField()
    total_kg = serializers.SerializerMethodField()
    total_amount = serializers.SerializerMethodField()
    shipped_at = serializers.SerializerMethodField()

    def get_client_name(self, order):
        return order.client.company_name.strip() or order.client.name

    def get_bags(self, order):
        return sum(item.quantity for item in order.items.all())

    def get_total_kg(self, order):
        total = sum((Decimal(item.product_weight_kg or 0) * item.quantity for item in order.items.all()), Decimal("0"))
        return money_string(total)

    def get_total_amount(self, order):
        return money_string(order.total_amount)

    def get_shipped_at(self, order):
        shipment = getattr(order, "shipment", None)
        return shipment.shipped_at if shipment else None


class LoaderDispatchSerializer(serializers.Serializer):
    truck_number = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")


class WaybillSignerSerializer(serializers.Serializer):
    role = serializers.CharField(max_length=40, allow_blank=True)
    name = serializers.CharField(max_length=60, allow_blank=True)


class WaybillSettingsSerializer(serializers.ModelSerializer):
    point_name = serializers.CharField(max_length=120)
    signers = WaybillSignerSerializer(many=True, max_length=6)

    class Meta:
        model = WaybillSettings
        fields = ["point_name", "signers", "updated_at"]
        read_only_fields = ["updated_at"]
