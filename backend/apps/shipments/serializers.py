from rest_framework import serializers
from .models import Shipment


class ArrivalSerializer(serializers.Serializer):
    weigh_in_kg = serializers.DecimalField(max_digits=12, decimal_places=2)


class LoadSerializer(serializers.Serializer):
    bags = serializers.IntegerField(min_value=0)


class ShipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shipment
        fields = ["id", "order", "truck_number", "weigh_in_kg",
                  "bags_loaded", "arrived_at", "shipped_at"]
