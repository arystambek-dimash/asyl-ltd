from rest_framework import serializers
from .models import Shipment


class ShipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shipment
        fields = ["id", "order", "truck_number", "weigh_in_kg", "weigh_out_kg",
                  "net_weight_kg", "bags_loaded", "arrived_at", "shipped_at"]
