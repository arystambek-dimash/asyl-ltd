from functools import cached_property

from rest_framework import serializers

from apps.cameras.models import VehiclePlateEvent

from .models import (
    PASSAGE_SCALE_MAX_STABLE_WEIGHT_SECONDS,
    PASSAGE_SCALE_MIN_STABLE_WEIGHT_SECONDS,
    GrainMovement,
    GrainSupply,
    PassageWeightCapture,
    Silo,
    SiloAllocation,
    SiloType,
    UnassignedWeighing,
    VehicleOrientationSample,
    Wagon,
    WagonArchStop,
    WeighingRecord,
)
from .orientation_dataset import load_records
from .photos import KIND_EVIDENCE, KIND_UNASSIGNED, KIND_WEIGHING, photo_url
from .weighing_photos import photo_delivery_status
from .statuses import FINISHED_STATUSES, WAGON_STATUS_LABELS


class SiloSerializer(serializers.ModelSerializer):
    current_balance_kg = serializers.IntegerField(read_only=True)
    reserved_kg = serializers.IntegerField(read_only=True)
    free_capacity_kg = serializers.IntegerField(read_only=True)
    fill_percent = serializers.SerializerMethodField()
    active_wagons = serializers.SerializerMethodField()
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
            "current_balance_kg",
            "reserved_kg",
            "free_capacity_kg",
            "fill_percent",
            "active_wagons",
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
        if hasattr(silo, "_active_wagons"):
            return [{"id": row.pk, "number": row.number, "status": row.status}
                    for row in silo._active_wagons]
        rows = silo.assigned_wagons.exclude(
            status__in=FINISHED_STATUSES,
        ).values("id", "number", "status")
        return list(rows)

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
    reference_record_source = serializers.CharField(source="reference_record.source", default=None, read_only=True)
    reference_record_at = serializers.DateTimeField(source="reference_record.created_at", default=None, read_only=True)
    operator_name = serializers.CharField(
        source="operator.username", default=None, read_only=True
    )
    photo_url = serializers.SerializerMethodField()
    photo_status = serializers.SerializerMethodField()

    def get_photo_status(self, record):
        return photo_delivery_status(record)

    class Meta:
        model = WeighingRecord
        fields = [
            "id",
            "kind",
            "weight_kg",
            "scale_number",
            "source",
            "reference_record",
            "reference_record_at",
            "reference_record_source",
            "manual_reason",
            "previous_weight_kg",
            "operator_name",
            "photo_url",
            "photo_status",
            "orientation",
            "created_at",
        ]

    def get_photo_url(self, record: WeighingRecord) -> str | None:
        return photo_url(KIND_WEIGHING, record)


class UnassignedWeighingSerializer(serializers.ModelSerializer):
    photo_url = serializers.SerializerMethodField()
    photo_status = serializers.SerializerMethodField()
    identity_check = serializers.SerializerMethodField()

    def get_identity_check(self, item):
        from .weighing_identity import public_status

        return public_status(item)

    def get_photo_status(self, item):
        return photo_delivery_status(item)

    wagon_number = serializers.CharField(
        source="wagon.number", default="", read_only=True
    )
    resolved_by_name = serializers.CharField(
        source="resolved_by.username", default=None, read_only=True
    )

    class Meta:
        model = UnassignedWeighing
        fields = [
            "id",
            "weight_kg",
            "stable_weight_at",
            "scale_number",
            "camera",
            "photo_url",
            "photo_status",
            "identity_check",
            "reason",
            "vehicle_number",
            "orientation",
            "status",
            "wagon",
            "wagon_number",
            "action",
            "resolved_by_name",
            "resolved_at",
            "created_at",
        ]

    def get_photo_url(self, item: UnassignedWeighing) -> str | None:
        return photo_url(KIND_UNASSIGNED, item)


class VehicleOrientationSampleSerializer(serializers.ModelSerializer):
    """Строка датасета ориентации для страницы разметки.

    Фото и номер машины живут на исходном взвешивании. Список кладёт строки
    страницы в ``context["records"]`` (см. ``load_records``), чтобы не ходить
    в базу за каждой; без контекста (деталь, ответ действия) запись грузится
    по одной.
    """

    sample_id = serializers.CharField(read_only=True)
    reviewed_by_name = serializers.CharField(
        source="reviewed_by.username", default=None, read_only=True
    )
    photo_url = serializers.SerializerMethodField()
    vehicle_number = serializers.SerializerMethodField()
    wagon = serializers.SerializerMethodField()

    class Meta:
        model = VehicleOrientationSample
        fields = [
            "id",
            "sample_id",
            "record_kind",
            "record_id",
            "label",
            "label_source",
            "weight_kg",
            "captured_at",
            "model_orientation",
            "conflict",
            "excluded",
            "sent_at",
            "delivered_at",
            "last_error",
            "reviewed_by_name",
            "reviewed_at",
            "photo_url",
            "vehicle_number",
            "wagon",
        ]

    @cached_property
    def _own_records(self) -> dict:
        return {}

    def _record(self, sample: VehicleOrientationSample):
        key = (sample.record_kind, sample.record_id)
        records = self.context.get("records")
        if records is None:
            records = self._own_records
            if key not in records:
                records.update(load_records([sample]))
                records.setdefault(key, None)
        return records.get(key)

    def get_photo_url(self, sample: VehicleOrientationSample) -> str | None:
        # record_kind совпадает с видом подписанной ссылки: weighing/unassigned.
        return photo_url(sample.record_kind, self._record(sample))

    def get_vehicle_number(self, sample: VehicleOrientationSample) -> str:
        record = self._record(sample)
        if record is None:
            return ""
        number = getattr(record, "vehicle_number", "")
        if not number and record.wagon_id:
            number = record.wagon.number
        return number or ""

    def get_wagon(self, sample: VehicleOrientationSample) -> int | None:
        record = self._record(sample)
        return record.wagon_id if record is not None else None


class PassageNumberSerializer(serializers.Serializer):
    number = serializers.CharField(max_length=30, allow_blank=False, trim_whitespace=True)


class UnassignedAssignSerializer(serializers.Serializer):
    wagon = serializers.IntegerField(min_value=1)


class HistoricalTareSerializer(serializers.Serializer):
    reference_record = serializers.IntegerField(min_value=1)
    number = serializers.CharField(max_length=30)
    reason = serializers.CharField()


class UnassignedCreatePassageSerializer(serializers.Serializer):
    number = serializers.CharField(
        max_length=30, allow_blank=True, required=False, default=""
    )
    cargo_name = serializers.CharField(
        max_length=100, allow_blank=True, required=False, default=""
    )


class UnassignedDiscardSerializer(serializers.Serializer):
    reason = serializers.CharField(
        max_length=200, allow_blank=True, required=False, default=""
    )


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
    allocations = SiloAllocationSerializer(many=True, read_only=True)
    # Сверка веса считается в модели (Wagon.weight_*) — тем же правилом,
    # что ставит статус «Расхождение веса».
    weight_difference_kg = serializers.IntegerField(read_only=True)
    weight_difference_percent = serializers.FloatField(read_only=True)
    weight_matches = serializers.BooleanField(read_only=True)
    vehicle_recognition_captures = serializers.SerializerMethodField()
    entry_photo_url = serializers.SerializerMethodField()
    exit_photo_url = serializers.SerializerMethodField()

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
            "allocations",
            "vehicle_recognition_captures",
            "entry_photo_url",
            "exit_photo_url",
        ]

    def _latest_photo_url(self, wagon: Wagon, kind: str) -> str | None:
        # ``weighings`` is prefetched and ordered by ``-id``: the first record
        # with a photo is the most recent weighing of that kind.
        for record in wagon.weighings.all():
            if record.kind == kind and record.photo:
                return photo_url(KIND_WEIGHING, record)
        return None

    def get_entry_photo_url(self, wagon: Wagon) -> str | None:
        return self._latest_photo_url(wagon, "gross")

    def get_exit_photo_url(self, wagon: Wagon) -> str | None:
        return self._latest_photo_url(wagon, "tare")

    def get_status_label(self, wagon: Wagon) -> str:
        if wagon.is_passage and wagon.status == "at_silo":
            return "На территории · погрузка"
        return WAGON_STATUS_LABELS.get(wagon.status, wagon.status)

    def get_vehicle_recognition_captures(self, wagon: Wagon) -> list[dict]:
        captures = wagon.passage_weight_captures.order_by("-id")[:10]
        return PassageWeightCaptureSerializer(captures, many=True).data


# Журналы, фото и выгрузка нужны только карточке вагона, не строке списка.
WAGON_DETAIL_ONLY_FIELDS = frozenset({
    "unloading_point",
    "unloading_started_at",
    "unloading_finished_at",
    "unloading_paused",
    "note",
    "weighings",
    "allocations",
    "vehicle_recognition_captures",
    "entry_photo_url",
    "exit_photo_url",
})


class WagonBriefSerializer(WagonSerializer):
    """Лёгкая строка для списков — без вложенных журналов."""

    class Meta(WagonSerializer.Meta):
        fields = [
            name for name in WagonSerializer.Meta.fields
            if name not in WAGON_DETAIL_ONLY_FIELDS
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
            "culture",
            "grain_class",
            "expected_total_kg",
            "note",
            "status",
            "created_at",
            "wagons",
        ]
        read_only_fields = ["simple_flow", "status", "created_at"]
        extra_kwargs = {
            "culture": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        grain_type = attrs.get("grain_type")
        silo = attrs.get("assigned_silo")
        expected = attrs.get("expected_total_kg")
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
        attrs["simple_flow"] = True
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


class WagonArchStopSerializer(serializers.ModelSerializer):
    net_kg = serializers.SerializerMethodField()
    wagon_status = serializers.CharField(source="wagon.status", default="", read_only=True)
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = WagonArchStop
        fields = ["id", "stop_id", "camera", "arrived_at", "full_weight_kg", "exit_weight_kg", "net_kg", "number",
                  "number_source", "recognition_error", "ocr_attempts", "status", "blocked_reason", "blocked_detail",
                  "motion_gap", "departed_at", "entry_applied_at", "exit_applied_at", "wagon_id", "wagon_status",
                  "continues", "photo_url"]

    def get_net_kg(self, stop):
        return stop.full_weight_kg - stop.exit_weight_kg if stop.exit_weight_kg is not None else None

    def get_photo_url(self, stop):
        delivery = self.context.get("deliveries", {}).get(stop.photo_request_id)
        return photo_url(KIND_EVIDENCE, delivery)
