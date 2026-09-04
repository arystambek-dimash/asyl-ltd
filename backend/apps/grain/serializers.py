from rest_framework import serializers

from apps.cameras.models import VehiclePlateEvent

from .models import (
    PASSAGE_SCALE_MAX_STABLE_WEIGHT_SECONDS,
    PASSAGE_SCALE_MIN_STABLE_WEIGHT_SECONDS,
    GrainMovement,
    GrainSettings,
    GrainSupply,
    LabCheck,
    PassageWeightCapture,
    Silo,
    SiloAllocation,
    SiloType,
    Wagon,
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
    silo_type_name = serializers.CharField(
        source="silo_type.name", default=None, read_only=True
    )
    silo_type_color = serializers.CharField(
        source="silo_type.color", default=None, read_only=True
    )
    is_default_route = serializers.SerializerMethodField()

    class Meta:
        model = Silo
        fields = [
            "id",
            "name",
            "total_capacity_kg",
            "silo_type",
            "silo_type_name",
            "silo_type_color",
            "is_default_route",
            "grain_culture",
            "grain_class",
            "allow_mixing",
            "is_quarantine",
            "status",
            "unloading_line",
            "sensor_estimated_kg",
            "current_balance_kg",
            "reserved_kg",
            "free_capacity_kg",
            "fill_percent",
            "active_wagons",
            "sensor_difference_kg",
        ]

    def validate(self, attrs):
        silo_type = attrs.get("silo_type", getattr(self.instance, "silo_type", None))
        if silo_type:
            attrs.setdefault("grain_culture", silo_type.grain_culture)
            attrs.setdefault("grain_class", silo_type.grain_class)
        return attrs

    def get_fill_percent(self, silo: Silo) -> int:
        if not silo.total_capacity_kg:
            return 0
        return round(silo.current_balance_kg * 100 / silo.total_capacity_kg)

    def get_active_wagons(self, silo: Silo):
        rows = silo.assigned_wagons.exclude(
            status__in=["completed", "cancelled", "return_to_supplier", "exited"],
        ).values("id", "number", "status")
        return list(rows)

    def get_sensor_difference_kg(self, silo: Silo):
        if silo.sensor_estimated_kg is None:
            return None
        return silo.sensor_estimated_kg - silo.current_balance_kg

    def get_is_default_route(self, silo: Silo) -> bool:
        return silo.default_for_types.exists()


class SiloTypeSerializer(serializers.ModelSerializer):
    default_silo_name = serializers.CharField(
        source="default_silo.name", default=None, read_only=True
    )
    silo_count = serializers.IntegerField(source="silos.count", read_only=True)

    class Meta:
        model = SiloType
        fields = [
            "id",
            "name",
            "grain_culture",
            "grain_class",
            "color",
            "description",
            "default_silo",
            "default_silo_name",
            "silo_count",
            "created_at",
        ]
        read_only_fields = ["created_at"]

    def validate_color(self, value):
        import re

        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise serializers.ValidationError("Укажите цвет в формате #RRGGBB")
        return value.upper()

    def validate(self, attrs):
        default_silo = attrs.get(
            "default_silo", getattr(self.instance, "default_silo", None)
        )
        if default_silo and default_silo.silo_type_id not in (
            None,
            getattr(self.instance, "pk", None),
        ):
            raise serializers.ValidationError(
                {
                    "default_silo": "Этот силос уже относится к другому типу.",
                }
            )
        return attrs

    def _sync_default_silo(self, instance):
        silo = instance.default_silo
        if silo and silo.silo_type_id != instance.pk:
            silo.silo_type = instance
            if not silo.grain_culture:
                silo.grain_culture = instance.grain_culture
            if not silo.grain_class:
                silo.grain_class = instance.grain_class
            silo.save(update_fields=["silo_type", "grain_culture", "grain_class"])

    def create(self, validated_data):
        instance = super().create(validated_data)
        self._sync_default_silo(instance)
        return instance

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        self._sync_default_silo(instance)
        return instance


class WeighingRecordSerializer(serializers.ModelSerializer):
    operator_name = serializers.CharField(
        source="operator.username", default=None, read_only=True
    )

    class Meta:
        model = WeighingRecord
        fields = [
            "id",
            "kind",
            "weight_kg",
            "scale_number",
            "source",
            "manual_reason",
            "previous_weight_kg",
            "operator_name",
            "created_at",
        ]


class LabCheckSerializer(serializers.ModelSerializer):
    checked_by_name = serializers.CharField(
        source="checked_by.username", default=None, read_only=True
    )

    class Meta:
        model = LabCheck
        fields = [
            "id",
            "moisture",
            "impurity",
            "nature",
            "grain_class",
            "infestation",
            "damage",
            "note",
            "decision",
            "checked_by_name",
            "created_at",
        ]


class SiloAllocationSerializer(serializers.ModelSerializer):
    silo_name = serializers.CharField(source="silo.name", read_only=True)

    class Meta:
        model = SiloAllocation
        fields = [
            "id",
            "silo",
            "silo_name",
            "amount_kg",
            "measurement_source",
            "created_at",
        ]


class PassageWeightCaptureSerializer(serializers.ModelSerializer):
    """Safe audit projection; raw model payload and secrets are never exposed."""

    request_id = serializers.UUIDField(source="idempotency_key", read_only=True)

    class Meta:
        model = PassageWeightCapture
        fields = [
            "request_id",
            "action",
            "status",
            "stage",
            "camera",
            "camera_source",
            "stable_weight_at",
            "weight_kg",
            "vehicle_number",
            "recognized_at",
            "confirmation_votes",
            "detector_confidence",
            "ocr_confidence",
            "response_status",
            "retryable",
            "error_code",
            "error_detail",
            "started_at",
            "updated_at",
            "completed_at",
        ]


class WagonSerializer(serializers.ModelSerializer):
    status_label = serializers.SerializerMethodField()
    supplier = serializers.CharField(
        source="supply.supplier", default="", read_only=True
    )
    culture = serializers.CharField(source="supply.culture", default="", read_only=True)
    grain_class = serializers.CharField(
        source="supply.grain_class", default="", read_only=True
    )
    grain_type = serializers.IntegerField(
        source="supply.grain_type_id", default=None, read_only=True
    )
    grain_type_name = serializers.CharField(
        source="supply.grain_type.name", default="", read_only=True
    )
    assigned_silo_name = serializers.CharField(
        source="assigned_silo.name", default=None, read_only=True
    )
    weighings = WeighingRecordSerializer(many=True, read_only=True)
    lab_checks = LabCheckSerializer(many=True, read_only=True)
    allocations = SiloAllocationSerializer(many=True, read_only=True)
    weight_difference_kg = serializers.SerializerMethodField()
    weight_difference_percent = serializers.SerializerMethodField()
    weight_matches = serializers.SerializerMethodField()
    vehicle_recognition_captures = serializers.SerializerMethodField()

    class Meta:
        model = Wagon
        fields = [
            "id",
            "supply",
            "number",
            "number_source",
            "number_camera_source",
            "workflow",
            "direction",
            "cargo_name",
            "status",
            "status_label",
            "unplanned",
            "supplier",
            "culture",
            "grain_class",
            "grain_type",
            "grain_type_name",
            "document_weight_kg",
            "expected_weight_kg",
            "arrived_at",
            "gross_weight_kg",
            "tare_weight_kg",
            "net_weight_kg",
            "entry_weight_kg",
            "exit_weight_kg",
            "weight_difference_kg",
            "weight_difference_percent",
            "weight_matches",
            "assigned_silo",
            "assigned_silo_name",
            "unloading_point",
            "unloading_started_at",
            "silo_arrived_at",
            "unloading_finished_at",
            "unloading_paused",
            "exited_at",
            "note",
            "created_at",
            "weighings",
            "lab_checks",
            "allocations",
            "vehicle_recognition_captures",
        ]

    def get_status_label(self, wagon: Wagon) -> str:
        if wagon.is_passage and wagon.status == "at_silo":
            return "На территории · погрузка"
        return WAGON_STATUS_LABELS.get(wagon.status, wagon.status)

    def get_vehicle_recognition_captures(self, wagon: Wagon) -> list[dict]:
        captures = wagon.passage_weight_captures.order_by("-id")[:10]
        return PassageWeightCaptureSerializer(captures, many=True).data

    def get_weight_difference_kg(self, wagon: Wagon):
        expected = wagon.planned_weight_kg
        if expected is None or wagon.net_weight_kg is None:
            return None
        return wagon.net_weight_kg - expected

    def get_weight_difference_percent(self, wagon: Wagon):
        difference = self.get_weight_difference_kg(wagon)
        expected = wagon.planned_weight_kg
        if difference is None or not expected:
            return None
        return round(difference * 100 / expected, 2)

    def get_weight_matches(self, wagon: Wagon):
        percent = self.get_weight_difference_percent(wagon)
        if percent is None:
            return None
        return abs(percent) <= float(GrainSettings.get().allowed_discrepancy_percent)


class WagonBriefSerializer(WagonSerializer):
    """Лёгкая строка для списков — без вложенных журналов."""

    class Meta(WagonSerializer.Meta):
        fields = [
            "id",
            "supply",
            "number",
            "number_source",
            "number_camera_source",
            "workflow",
            "direction",
            "cargo_name",
            "status",
            "status_label",
            "unplanned",
            "supplier",
            "culture",
            "grain_class",
            "grain_type",
            "grain_type_name",
            "document_weight_kg",
            "expected_weight_kg",
            "arrived_at",
            "gross_weight_kg",
            "tare_weight_kg",
            "net_weight_kg",
            "entry_weight_kg",
            "exit_weight_kg",
            "weight_difference_kg",
            "weight_difference_percent",
            "weight_matches",
            "assigned_silo",
            "assigned_silo_name",
            "silo_arrived_at",
            "exited_at",
            "created_at",
        ]


class AutomaticPassageScaleSettingsSerializer(serializers.Serializer):
    stable_weight_seconds = serializers.JSONField()

    def validate(self, attrs):
        value = attrs.get("stable_weight_seconds")
        if type(value) is not int or not (
            PASSAGE_SCALE_MIN_STABLE_WEIGHT_SECONDS
            <= value
            <= PASSAGE_SCALE_MAX_STABLE_WEIGHT_SECONDS
        ):
            raise serializers.ValidationError(
                {
                    "stable_weight_seconds": (
                        "Укажите целое число секунд от "
                        f"{PASSAGE_SCALE_MIN_STABLE_WEIGHT_SECONDS} до "
                        f"{PASSAGE_SCALE_MAX_STABLE_WEIGHT_SECONDS}."
                    ),
                    "code": "bad_stable_weight_seconds",
                }
            )
        return {"stable_weight_seconds": value}


class VehiclePlateCandidateSerializer(serializers.ModelSerializer):
    """Minimal event projection exposed to gate operators."""

    stationary_seconds = serializers.FloatField(read_only=True)
    ocr_confidence = serializers.FloatField(read_only=True)

    class Meta:
        model = VehiclePlateEvent
        fields = [
            "event_id",
            "vehicle_number",
            "camera",
            "source",
            "detected_at",
            "stationary_seconds",
            "ocr_confidence",
        ]


class GrainSupplySerializer(serializers.ModelSerializer):
    wagons = WagonBriefSerializer(many=True, read_only=True)
    wagon_numbers = serializers.ListField(
        child=serializers.CharField(allow_blank=True), write_only=True, required=False
    )
    grain_type_name = serializers.CharField(
        source="grain_type.name", default="", read_only=True
    )
    grain_type_color = serializers.CharField(
        source="grain_type.color", default=None, read_only=True
    )
    assigned_silo_name = serializers.CharField(
        source="assigned_silo.name", default=None, read_only=True
    )

    class Meta:
        model = GrainSupply
        fields = [
            "id",
            "supplier",
            "grain_type",
            "grain_type_name",
            "grain_type_color",
            "assigned_silo",
            "assigned_silo_name",
            "simple_flow",
            "contract",
            "culture",
            "grain_class",
            "expected_date",
            "expected_total_kg",
            "document_weight_kg",
            "wagons_expected",
            "note",
            "status",
            "created_at",
            "wagons",
            "wagon_numbers",
        ]
        read_only_fields = ["status", "created_at"]
        extra_kwargs = {
            "culture": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        simple = attrs.get("simple_flow", getattr(self.instance, "simple_flow", False))
        if not simple:
            return attrs
        grain_type = attrs.get("grain_type", getattr(self.instance, "grain_type", None))
        silo = attrs.get("assigned_silo", getattr(self.instance, "assigned_silo", None))
        expected = attrs.get(
            "expected_total_kg",
            getattr(self.instance, "expected_total_kg", None),
        )
        errors = {}
        if not grain_type:
            errors["grain_type"] = "Выберите тип зерна"
        if not silo:
            errors["assigned_silo"] = "Выберите силос назначения"
        if not expected:
            errors["expected_total_kg"] = "Укажите ожидаемый вес"
        if silo and grain_type and silo.silo_type_id not in (None, grain_type.pk):
            errors["assigned_silo"] = "Тип зерна не совпадает с типом силоса"
        if errors:
            raise serializers.ValidationError(errors)
        # Старые строки остаются заполнены только для совместимости с историей.
        attrs.setdefault("culture", grain_type.name)
        attrs.setdefault("grain_class", "")
        attrs.setdefault("wagons_expected", 1)
        return attrs


class GrainMovementSerializer(serializers.ModelSerializer):
    silo_name = serializers.CharField(source="silo.name", read_only=True)
    wagon_number = serializers.CharField(
        source="wagon.number", default=None, read_only=True
    )
    created_by_name = serializers.CharField(
        source="created_by.username", default=None, read_only=True
    )

    class Meta:
        model = GrainMovement
        fields = [
            "id",
            "silo",
            "silo_name",
            "movement_type",
            "delta_kg",
            "balance_after_kg",
            "wagon",
            "wagon_number",
            "batch_number",
            "note",
            "created_by_name",
            "created_at",
        ]
