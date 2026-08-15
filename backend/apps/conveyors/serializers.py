from collections.abc import Mapping

from rest_framework import serializers

from apps.cameras import ai

from .models import MAX_SEQUENCE, ConveyorDevice

MAX_COUNTER_TOTAL = 2_147_483_647
TERMINAL_REASONS = (
    "target_reached",
    "stale_ai",
    "no_progress",
    "max_runtime",
    "processor_stopped",
    "capture_failed",
    "session_setup_failed",
    "counter_regressed",
    "manual_stop",
    "controller_fault",
    "shutdown",
)


class StrictIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if type(data) is not int:
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictCharField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictUUIDField(serializers.UUIDField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        value = super().to_internal_value(data)
        if data != str(value):
            self.fail("invalid")
        return value


class ForbidUnknownSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({"detail": "Expected an object"})
        unknown = sorted(set(data) - set(self.fields))
        if unknown:
            raise serializers.ValidationError(
                {"detail": f"Unknown fields: {', '.join(unknown)}"}
            )
        return super().to_internal_value(data)


class DeviceSyncSerializer(ForbidUnknownSerializer):
    protocol_version = StrictIntegerField(min_value=1, max_value=1)
    boot_id = StrictUUIDField()
    seq = StrictIntegerField(min_value=0, max_value=MAX_SEQUENCE)
    ack_revision = StrictIntegerField(
        min_value=1, max_value=MAX_SEQUENCE, allow_null=True,
    )
    output_state = StrictIntegerField(min_value=0, max_value=1)
    feedback_state = StrictIntegerField(min_value=0, max_value=1)
    fault = StrictCharField(
        max_length=128, allow_blank=False, allow_null=True,
    )
    uptime_ms = StrictIntegerField(
        min_value=0, max_value=MAX_SEQUENCE, required=False,
    )
    wifi_rssi = StrictIntegerField(
        min_value=-127, max_value=0, required=False,
    )
    firmware = StrictCharField(
        max_length=64, allow_blank=True, required=False,
    )


class AiObservationSerializer(ForbidUnknownSerializer):
    protocol_version = StrictIntegerField(min_value=1, max_value=1)
    camera = StrictCharField(max_length=32)
    session_id = StrictIntegerField(min_value=1, max_value=MAX_COUNTER_TOTAL)
    target_total = StrictIntegerField(min_value=1, max_value=MAX_COUNTER_TOTAL)
    edge_boot_id = StrictUUIDField()
    seq = StrictIntegerField(min_value=0, max_value=MAX_SEQUENCE)
    total = StrictIntegerField(min_value=0, max_value=MAX_COUNTER_TOTAL)
    terminal_reason = serializers.ChoiceField(
        choices=TERMINAL_REASONS,
        allow_null=True,
    )

    def validate_camera(self, value):
        try:
            camera = ai.normalize(value)
        except ai.AiError as exc:
            raise serializers.ValidationError("Unknown camera") from exc
        if value != camera:
            raise serializers.ValidationError("Camera must be canonical camN")
        return camera


class ConveyorDeviceEnrollSerializer(ForbidUnknownSerializer):
    name = StrictCharField(max_length=80, trim_whitespace=True)
    camera_source = StrictCharField(max_length=32)
    is_active = serializers.BooleanField(default=True)

    def validate_name(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError("Name is required")
        return value

    def validate_camera_source(self, value):
        try:
            camera = ai.normalize(value)
        except ai.AiError as exc:
            raise serializers.ValidationError("Unknown camera") from exc
        if value != camera:
            raise serializers.ValidationError("Camera must be canonical camN")
        return camera

    def validate_is_active(self, value):
        if type(self.initial_data.get("is_active", True)) is not bool:
            raise serializers.ValidationError("Must be a boolean")
        return value


class ConveyorDeviceUpdateSerializer(ForbidUnknownSerializer):
    name = StrictCharField(max_length=80, trim_whitespace=True, required=False)
    camera_source = StrictCharField(max_length=32, required=False)
    is_active = serializers.BooleanField(required=False)

    def validate_name(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError("Name is required")
        return value

    def validate_camera_source(self, value):
        try:
            camera = ai.normalize(value)
        except ai.AiError as exc:
            raise serializers.ValidationError("Unknown camera") from exc
        if value != camera:
            raise serializers.ValidationError("Camera must be canonical camN")
        return camera

    def validate_is_active(self, value):
        if type(self.initial_data.get("is_active")) is not bool:
            raise serializers.ValidationError("Must be a boolean")
        return value


def device_payload(device: ConveyorDevice) -> dict:
    return {
        "id": device.pk,
        "public_id": str(device.public_id),
        "name": device.name,
        "camera_source": device.camera_source,
        "is_active": device.is_active,
        "desired_state": int(device.desired_state),
        "command_revision": device.command_revision,
        "command_session_id": device.command_session_id,
        "command_target_total": device.command_target_total,
        "command_terminal": device.command_terminal,
        "stop_reason": device.stop_reason,
        "last_seen_at": device.last_seen_at,
        "output_state": (
            None if device.output_state is None else int(device.output_state)
        ),
        "feedback_state": (
            None if device.feedback_state is None else int(device.feedback_state)
        ),
        "fault": device.fault or None,
        "firmware": device.firmware,
        "wifi_rssi": device.wifi_rssi,
        "last_ai_seen_at": device.last_ai_seen_at,
        "last_total": device.last_total,
        "created_at": device.created_at,
        "updated_at": device.updated_at,
    }
