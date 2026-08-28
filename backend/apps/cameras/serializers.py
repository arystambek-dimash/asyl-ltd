"""Validation for camera control-plane write endpoints.

These serializers deliberately validate inputs only. Database writes, audit
events and calls to the camera PC belong to the view/workflow layer, where
they can be coordinated explicitly.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from . import ai, services
from .models import MonoblockDevice


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


class MonoblockDeviceCreateUpdateSerializer(serializers.Serializer):
    """Validate a dedicated monoblock account without mutating its User."""

    name = serializers.JSONField(required=False, allow_null=True)
    username = serializers.JSONField(required=False, allow_null=True)
    camera_source = serializers.JSONField(required=False, allow_null=True)
    password = serializers.JSONField(required=False, allow_null=True)
    is_active = serializers.JSONField(required=False, allow_null=True)

    def validate(self, attrs):
        device = self.instance
        name = self._clean_text(
            attrs.get("name", getattr(device, "name", "")),
            detail="Название обязательно, максимум 80 символов",
            code="bad_name",
            max_length=80,
        )
        username = self._clean_text(
            attrs.get(
                "username",
                getattr(getattr(device, "user", None), "username", ""),
            ),
            detail="Логин обязателен, максимум 150 символов",
            code="bad_username",
            max_length=150,
        )
        camera = self._camera(
            attrs.get("camera_source", getattr(device, "camera_source", ""))
        )

        self._validate_unique_username(username, device)
        self._validate_unique_camera(camera, device)

        result = {
            "name": name,
            "username": username,
            "camera_source": camera,
        }
        if "is_active" in attrs:
            if type(attrs["is_active"]) is not bool:
                raise serializers.ValidationError(
                    {
                        "detail": "Передайте true или false",
                        "code": "bad_is_active",
                    }
                )
            result["is_active"] = attrs["is_active"]
        elif device is None:
            result["is_active"] = True

        password = attrs.get("password", "")
        if device is None or password not in (None, ""):
            if not isinstance(password, str):
                self._raise_weak_password(["Пароль должен быть строкой."])
            self._validate_password(password, username, device)
            result["password"] = password
        return result

    @staticmethod
    def _clean_text(value, *, detail, code, max_length):
        if not isinstance(value, str):
            raise serializers.ValidationError({"detail": detail, "code": code})
        value = " ".join(value.split())
        if not value or len(value) > max_length:
            raise serializers.ValidationError({"detail": detail, "code": code})
        return value

    @staticmethod
    def _camera(raw_camera):
        if not isinstance(raw_camera, str):
            raise serializers.ValidationError(
                {"detail": "Выберите корректную камеру", "code": "bad_camera"}
            )
        try:
            return ai.normalize(raw_camera)
        except ai.AiError as exc:
            raise serializers.ValidationError(
                {"detail": "Выберите корректную камеру", "code": "bad_camera"}
            ) from exc

    @staticmethod
    def _validate_unique_username(username, device):
        users = get_user_model().objects.filter(username__iexact=username)
        if device is not None:
            users = users.exclude(pk=device.user_id)
        if users.exists():
            raise serializers.ValidationError(
                {"detail": "Такой логин уже используется", "code": "username_busy"}
            )

    @staticmethod
    def _validate_unique_camera(camera, device):
        devices = MonoblockDevice.objects.filter(camera_source=camera)
        if device is not None:
            devices = devices.exclude(pk=device.pk)
        if devices.exists():
            raise serializers.ValidationError(
                {
                    "detail": "Камера уже закреплена за другим моноблоком",
                    "code": "camera_busy",
                }
            )

    @classmethod
    def _validate_password(cls, password, username, device):
        User = get_user_model()
        current_user = getattr(device, "user", None)
        candidate = User(
            username=username,
            first_name=getattr(current_user, "first_name", ""),
            last_name=getattr(current_user, "last_name", ""),
            email=getattr(current_user, "email", ""),
        )
        try:
            validate_password(password, user=candidate)
        except DjangoValidationError as exc:
            cls._raise_weak_password(exc.messages)

    @staticmethod
    def _raise_weak_password(messages):
        raise serializers.ValidationError(
            {"detail": "; ".join(messages), "code": "weak_password"}
        )


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
