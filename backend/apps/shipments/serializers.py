from rest_framework import serializers
from .models import Shipment


class ArrivalSerializer(serializers.Serializer):
    # Вес спрашивается только для товаров с флагом ask_truck_weight; иначе
    # въезд без веса, и пост подставит расчётный вес по мешкам.
    weigh_in_kg = serializers.DecimalField(max_digits=12, decimal_places=2,
                                           required=False, allow_null=True)


class LoadSerializer(serializers.Serializer):
    bags = serializers.IntegerField(min_value=0)


class ShipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shipment
        fields = ["id", "order", "truck_number", "weigh_in_kg",
                  "bags_loaded", "arrived_at", "shipped_at"]
