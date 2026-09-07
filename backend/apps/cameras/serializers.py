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


class AlwaysOnAnalyticsSubtractSerializer(serializers.Serializer):
    amount = serializers.JSONField(required=False, allow_null=True)
    reason = serializers.JSONField(required=False, allow_null=True)
    color = serializers.CharField(max_length=32)

    def validate(self, attrs):
        raw_amount = attrs.get("amount")
        if type(raw_amount) is not int or raw_amount <= 0:
            raise serializers.ValidationError(
                {"amount": "Укажите количество больше нуля"}
            )
        amount = raw_amount

        raw_reason = attrs.get("reason", "")
        if not isinstance(raw_reason, str):
            reason = ""
        else:
            reason = " ".join(raw_reason.split())
        if len(reason) < 5:
            raise serializers.ValidationError(
                {"reason": "Укажите причину (минимум 5 символов)"}
            )
        if len(reason) > 500:
            raise serializers.ValidationError({"reason": "Причина слишком длинная"})
        color = " ".join(str(attrs.get("color") or "").split()).lower()
        if not color:
            raise serializers.ValidationError({"color": "Выберите цвет продукции"})
        return {"amount": amount, "reason": reason, "color": color}


class AlwaysOnAnalyticsArchiveSerializer(serializers.Serializer):
    note = serializers.JSONField(required=False, allow_null=True, default="")

    def validate_note(self, value):
        if value is None:
            return ""
        if not isinstance(value, str):
            raise serializers.ValidationError("Передайте примечание строкой")
        note = " ".join(value.split())
        if len(note) > 500:
            raise serializers.ValidationError("Примечание слишком длинное")
        return note


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


class ShippingBoardSettingsSerializer(serializers.Serializer):
    completed_orders_days = serializers.JSONField(required=False, allow_null=True)

    def validate(self, attrs):
        raw_value = attrs.get("completed_orders_days")
        if type(raw_value) is bool or not isinstance(raw_value, (int, str)):
            raise serializers.ValidationError(
                {
                    "completed_orders_days": "Укажите количество дней от 1 до 90",
                    "code": "bad_completed_orders_days",
                }
            )
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise serializers.ValidationError(
                {
                    "completed_orders_days": "Укажите количество дней от 1 до 90",
                    "code": "bad_completed_orders_days",
                }
            ) from exc
        if value < 1 or value > 90:
            raise serializers.ValidationError(
                {
                    "completed_orders_days": "Допустимо от 1 до 90 дней",
                    "code": "bad_completed_orders_days",
                }
            )
        return {"completed_orders_days": value}


class CameraAiActionSerializer(serializers.Serializer):
    """Common body/query values for start, reset and stop operations."""

    order_id = serializers.IntegerField(min_value=1)
    session_id = serializers.IntegerField(min_value=1, required=False)
    complete_order = serializers.BooleanField(required=False, default=False)


class AnalyticsRangeSerializer(serializers.Serializer):
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    camera = serializers.CharField(required=False, max_length=32)

    def validate_camera(self, value):
        try:
            return ai.normalize(value)
        except ai.AiError as exc:
            raise serializers.ValidationError("Неизвестная камера") from exc

    def validate(self, attrs):
        from django.utils import timezone
        if "date_from" in attrs or "date_to" in attrs:
            start = attrs.setdefault("date_from", timezone.localdate())
            end = attrs.setdefault("date_to", timezone.localdate())
            if start > end or (end - start).days >= 366:
                raise serializers.ValidationError("Выберите период от 1 до 366 дней")
        return attrs
