from rest_framework import serializers

from .models import (
    GrainMovement, GrainSupply, LabCheck, Silo, SiloAllocation, Wagon,
    WeighingRecord,
)
from .statuses import WAGON_STATUS_LABELS


class SiloSerializer(serializers.ModelSerializer):
    current_balance_kg = serializers.IntegerField(read_only=True)
    reserved_kg = serializers.IntegerField(read_only=True)
    free_capacity_kg = serializers.IntegerField(read_only=True)
    fill_percent = serializers.SerializerMethodField()
    active_wagons = serializers.SerializerMethodField()
    sensor_difference_kg = serializers.SerializerMethodField()

    class Meta:
        model = Silo
        fields = [
            "id", "name", "total_capacity_kg", "grain_culture", "grain_class",
            "allow_mixing", "is_quarantine", "status", "unloading_line",
            "sensor_estimated_kg", "current_balance_kg", "reserved_kg",
            "free_capacity_kg", "fill_percent", "active_wagons",
            "sensor_difference_kg",
        ]

    def get_fill_percent(self, silo: Silo) -> int:
        if not silo.total_capacity_kg:
            return 0
        return round(silo.current_balance_kg * 100 / silo.total_capacity_kg)

    def get_active_wagons(self, silo: Silo):
        rows = silo.assigned_wagons.exclude(
            status__in=["completed", "cancelled", "return_to_supplier",
                        "exited"],
        ).values("id", "number", "status")
        return list(rows)

    def get_sensor_difference_kg(self, silo: Silo):
        if silo.sensor_estimated_kg is None:
            return None
        return silo.sensor_estimated_kg - silo.current_balance_kg


class WeighingRecordSerializer(serializers.ModelSerializer):
    operator_name = serializers.CharField(
        source="operator.username", default=None, read_only=True)

    class Meta:
        model = WeighingRecord
        fields = [
            "id", "kind", "weight_kg", "scale_number", "source",
            "manual_reason", "previous_weight_kg", "operator_name",
            "created_at",
        ]


class LabCheckSerializer(serializers.ModelSerializer):
    checked_by_name = serializers.CharField(
        source="checked_by.username", default=None, read_only=True)

    class Meta:
        model = LabCheck
        fields = [
            "id", "moisture", "impurity", "nature", "grain_class",
            "infestation", "damage", "note", "decision", "checked_by_name",
            "created_at",
        ]


class SiloAllocationSerializer(serializers.ModelSerializer):
    silo_name = serializers.CharField(source="silo.name", read_only=True)

    class Meta:
        model = SiloAllocation
        fields = [
            "id", "silo", "silo_name", "amount_kg", "measurement_source",
            "created_at",
        ]


class WagonSerializer(serializers.ModelSerializer):
    status_label = serializers.SerializerMethodField()
    supplier = serializers.CharField(
        source="supply.supplier", default="", read_only=True)
    culture = serializers.CharField(
        source="supply.culture", default="", read_only=True)
    grain_class = serializers.CharField(
        source="supply.grain_class", default="", read_only=True)
    assigned_silo_name = serializers.CharField(
        source="assigned_silo.name", default=None, read_only=True)
    weighings = WeighingRecordSerializer(many=True, read_only=True)
    lab_checks = LabCheckSerializer(many=True, read_only=True)
    allocations = SiloAllocationSerializer(many=True, read_only=True)

    class Meta:
        model = Wagon
        fields = [
            "id", "supply", "number", "status", "status_label", "unplanned",
            "supplier", "culture", "grain_class",
            "document_weight_kg", "expected_weight_kg",
            "arrived_at", "gross_weight_kg", "tare_weight_kg", "net_weight_kg",
            "assigned_silo", "assigned_silo_name", "unloading_point",
            "unloading_started_at", "unloading_finished_at",
            "unloading_paused", "exited_at", "note", "created_at",
            "weighings", "lab_checks", "allocations",
        ]

    def get_status_label(self, wagon: Wagon) -> str:
        return WAGON_STATUS_LABELS.get(wagon.status, wagon.status)


class WagonBriefSerializer(WagonSerializer):
    """Лёгкая строка для списков — без вложенных журналов."""

    class Meta(WagonSerializer.Meta):
        fields = [
            "id", "supply", "number", "status", "status_label", "unplanned",
            "supplier", "culture", "grain_class",
            "document_weight_kg", "expected_weight_kg",
            "arrived_at", "gross_weight_kg", "tare_weight_kg", "net_weight_kg",
            "assigned_silo", "assigned_silo_name", "exited_at", "created_at",
        ]


class GrainSupplySerializer(serializers.ModelSerializer):
    wagons = WagonBriefSerializer(many=True, read_only=True)
    wagon_numbers = serializers.ListField(
        child=serializers.CharField(allow_blank=True), write_only=True,
        required=False)

    class Meta:
        model = GrainSupply
        fields = [
            "id", "supplier", "contract", "culture", "grain_class",
            "expected_date", "expected_total_kg", "document_weight_kg",
            "wagons_expected", "note", "status", "created_at",
            "wagons", "wagon_numbers",
        ]
        read_only_fields = ["status", "created_at"]


class GrainMovementSerializer(serializers.ModelSerializer):
    silo_name = serializers.CharField(source="silo.name", read_only=True)
    wagon_number = serializers.CharField(
        source="wagon.number", default=None, read_only=True)
    created_by_name = serializers.CharField(
        source="created_by.username", default=None, read_only=True)

    class Meta:
        model = GrainMovement
        fields = [
            "id", "silo", "silo_name", "movement_type", "delta_kg",
            "balance_after_kg", "wagon", "wagon_number", "batch_number",
            "note", "created_by_name", "created_at",
        ]
