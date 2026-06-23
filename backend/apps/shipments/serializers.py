from rest_framework import serializers
from .models import Shipment


class ArrivalSerializer(serializers.Serializer):
    weigh_in_kg = serializers.DecimalField(max_digits=12, decimal_places=2)
    debt_override = serializers.BooleanField(required=False, default=False)


class LoadSerializer(serializers.Serializer):
    bags = serializers.IntegerField(min_value=0)


class ShipSerializer(serializers.Serializer):
    weigh_out_kg = serializers.DecimalField(max_digits=12, decimal_places=2)


class ShipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shipment
        fields = ["id", "order", "truck_number", "weigh_in_kg", "weigh_out_kg",
                  "net_weight_kg", "bags_loaded", "arrived_at", "shipped_at"]
