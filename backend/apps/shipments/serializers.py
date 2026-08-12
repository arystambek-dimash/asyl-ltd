from rest_framework import serializers
from .models import Shipment


class ArrivalSerializer(serializers.Serializer):
    # Обычно поле не передаётся: backend сам читает физические весы. Ручное
    # значение оставлено как совместимый аварийный путь для старого поста.
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
            "weigh_in_source",
            "weigh_out_kg",
            "net_weight_kg",
            "bags_loaded",
            "arrived_at",
            "shipped_at",
        ]
