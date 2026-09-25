"""Validation for camera control-plane write endpoints.

These serializers deliberately validate inputs only. Database writes, audit
events and calls to the camera PC belong to the view/workflow layer, where
they can be coordinated explicitly.
"""

from rest_framework import serializers

from . import ai, services


class CameraRenameSerializer(serializers.Serializer):
    camera = serializers.JSONField(required=False, allow_null=True)
    name = serializers.JSONField(required=False, allow_null=True)

    def validate(self, attrs):
        raw_camera = attrs.get("camera")
        raw_name = attrs.get("name")
        if not isinstance(raw_camera, str) or not isinstance(raw_name, str):
            raise serializers.ValidationError(
                {
                    "detail": "Передайте камеру и новое имя",
                    "code": "bad_camera_name",
                }
            )

        try:
            camera = services.normalize_camera_path(raw_camera)
        except ValueError as exc:
            raise serializers.ValidationError(
                {"detail": "Неизвестная камера", "code": "bad_camera"}
            ) from exc

        name = " ".join(raw_name.split())
        if not name:
            raise serializers.ValidationError(
                {
                    "detail": "Название камеры не может быть пустым",
                    "code": "empty_camera_name",
                }
            )
        if len(name) > 80:
            raise serializers.ValidationError(
                {
                    "detail": "Название камеры не должно превышать 80 символов",
                    "code": "camera_name_too_long",
                }
            )
        return {"camera": camera, "name": name}


class CameraSourcesSerializer(serializers.Serializer):
    """Normalize an ordered, duplicate-free list of AI camera identifiers."""

    camera_sources = serializers.JSONField(required=False, allow_null=True)

    def validate(self, attrs):
        raw_sources = attrs.get("camera_sources")
        if not isinstance(raw_sources, list):
            raise serializers.ValidationError(
                {
                    "camera_sources": "Передайте список камер",
                    "code": "bad_camera_sources",
                }
            )

        sources = []
        for raw_source in raw_sources:
            if not isinstance(raw_source, str):
                raise serializers.ValidationError(
                    {
                        "camera_sources": "Каждая камера должна быть строкой",
                        "code": "bad_camera_source",
                    }
                )
            try:
                source = ai.normalize(raw_source)
            except ai.AiError as exc:
                raise serializers.ValidationError(
                    {
                        "camera_sources": f"Неизвестная камера: {raw_source}",
                        "code": "bad_camera_source",
                    }
                ) from exc
            if source not in sources:
                sources.append(source)
        return {"camera_sources": sources}


class WagonNumberCameraSettingsSerializer(serializers.Serializer):
    camera_source = serializers.JSONField(required=False, allow_null=True)

    def validate(self, attrs):
        raw_source = attrs.get("camera_source")
        if raw_source in (None, ""):
            return {"camera_source": ""}
        if not isinstance(raw_source, str):
            raise serializers.ValidationError(
                {
                    "camera_source": "Передайте камеру или null",
                    "code": "bad_camera_source",
                }
            )
        try:
            source = ai.normalize(raw_source)
        except ai.AiError as exc:
            raise serializers.ValidationError(
                {
                    "camera_source": f"Неизвестная камера: {raw_source}",
                    "code": "bad_camera_source",
                }
            ) from exc
        return {"camera_source": source}


class AlwaysOnProductMappingItemSerializer(serializers.Serializer):
    color = serializers.CharField(max_length=32)
    product = serializers.IntegerField(min_value=1, allow_null=True)

    def validate_color(self, value):
        color = " ".join(value.split()).lower()
        if not color:
            raise serializers.ValidationError("Укажите цвет")
        return color


class AlwaysOnProductMappingsSerializer(serializers.Serializer):
    camera = serializers.CharField(max_length=32)
    # Optional for rolling-deploy compatibility.  New clients always send it;
    # older clients keep routing production to the configured/default store.
    warehouse = serializers.IntegerField(min_value=1, required=False)
    mappings = AlwaysOnProductMappingItemSerializer(many=True)

    def validate_mappings(self, rows):
        colors = [row["color"] for row in rows]
        if len(colors) != len(set(colors)):
            raise serializers.ValidationError("Цвет передан повторно")
        return rows


class AlwaysOnUnknownColorSerializer(serializers.Serializer):
    """«Указать цвет» for bags left without a colour in one AI 24/7 shift."""

    camera = serializers.CharField(max_length=32)
    business_day = serializers.DateField()
    color = serializers.CharField(max_length=32)
    bags = serializers.IntegerField(min_value=1, max_value=100_000)
    reason = serializers.CharField(max_length=500)


class ShippingBoardSettingsSerializer(serializers.Serializer):
    completed_orders_days = serializers.IntegerField(
        min_value=1,
        max_value=90,
        error_messages={
            "required": "Укажите количество дней от 1 до 90",
            "null": "Укажите количество дней от 1 до 90",
            "invalid": "Укажите количество дней от 1 до 90",
            "min_value": "Допустимо от 1 до 90 дней",
            "max_value": "Допустимо от 1 до 90 дней",
        },
    )


class AiCameraSerializerMixin:
    """``camera`` query field normalized to a known AI camera id."""

    def validate_camera(self, value):
        try:
            return ai.normalize(value)
        except ai.AiError as exc:
            raise serializers.ValidationError("Неизвестная камера") from exc


class AnalyticsRangeSerializer(AiCameraSerializerMixin, serializers.Serializer):
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    camera = serializers.CharField(required=False, max_length=32)

    def validate(self, attrs):
        from django.utils import timezone

        if "date_from" in attrs or "date_to" in attrs:
            start = attrs.setdefault("date_from", timezone.localdate())
            end = attrs.setdefault("date_to", timezone.localdate())
            if start > end or (end - start).days >= 366:
                raise serializers.ValidationError("Выберите период от 1 до 366 дней")
        return attrs


class ShippingHistorySerializer(AiCameraSerializerMixin, serializers.Serializer):
    camera = serializers.CharField(max_length=32)
    day = serializers.DateField()

    def validate_day(self, value):
        from datetime import date

        # UTC conversion and the exclusive next midnight need representable
        # neighbouring days at both ends of Python's calendar.
        if value in (date.min, date.max):
            raise serializers.ValidationError(
                "Выберите дату от 02.01.0001 до 30.12.9999"
            )
        return value
