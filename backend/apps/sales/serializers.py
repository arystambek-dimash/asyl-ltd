from uuid import uuid4

from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from .models import Department

APIPAY_WRITE_FIELDS = ("apipay_api_key", "apipay_webhook_secret")
APIPAY_MIN_KEY_LENGTH = 8


class DepartmentSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True)
    order_count = serializers.SerializerMethodField()
    # Ключ ApiPay и секрет вебхука только на запись и только суперюзеру;
    # наружу уходит лишь факт подключения и хвост ключа для узнавания.
    apipay_configured = serializers.BooleanField(read_only=True)
    apipay_webhook_configured = serializers.SerializerMethodField()
    apipay_key_hint = serializers.SerializerMethodField()
    apipay_api_key = serializers.CharField(
        write_only=True, required=False, allow_blank=True, allow_null=True,
        max_length=255, trim_whitespace=True,
    )
    apipay_webhook_secret = serializers.CharField(
        write_only=True, required=False, allow_blank=True, allow_null=True,
        max_length=255, trim_whitespace=True,
    )

    class Meta:
        model = Department
        fields = [
            "id",
            "code",
            "name",
            "color",
            "is_active",
            "is_default",
            "order_count",
            "created_at",
            "apipay_configured",
            "apipay_webhook_configured",
            "apipay_key_hint",
            "apipay_updated_at",
            "apipay_api_key",
            "apipay_webhook_secret",
        ]
        read_only_fields = ["created_at", "apipay_updated_at"]

    def get_order_count(self, obj):
        counts = self.context.get("department_order_counts", {})
        return counts.get(obj.code, 0)

    def get_apipay_webhook_configured(self, obj) -> bool:
        return bool(obj.apipay_webhook_secret)

    def get_apipay_key_hint(self, obj) -> str:
        key = obj.apipay_api_key
        return f"••••{key[-4:]}" if key else ""

    def _is_superuser(self) -> bool:
        request = self.context.get("request")
        return bool(request and getattr(request.user, "is_superuser", False))

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self._is_superuser():
            data.pop("apipay_key_hint", None)
        return data

    def validate_apipay_api_key(self, value):
        value = (value or "").strip()
        if value and len(value) < APIPAY_MIN_KEY_LENGTH:
            raise serializers.ValidationError("Слишком короткий ключ ApiPay")
        if value and any(char.isspace() for char in value):
            raise serializers.ValidationError("Ключ ApiPay не содержит пробелов")
        return value

    def validate_apipay_webhook_secret(self, value):
        return (value or "").strip()

    def validate(self, attrs):
        touches_keys = any(field in attrs for field in APIPAY_WRITE_FIELDS)
        if touches_keys and not self._is_superuser():
            raise PermissionDenied({
                "detail": "Ключ ApiPay может менять только администратор",
                "code": "apipay_superuser_only",
            })
        if attrs.get("apipay_webhook_secret"):
            if "apipay_api_key" in attrs:
                has_key = bool(attrs["apipay_api_key"])
            else:
                has_key = bool(self.instance and self.instance.apipay_configured)
            if not has_key:
                raise serializers.ValidationError({
                    "detail": "Сначала укажите API-ключ отдела",
                    "code": "apipay_secret_without_key",
                })
        return attrs

    @staticmethod
    def _apply_apipay(department: Department, validated_data: dict) -> None:
        key = validated_data.pop("apipay_api_key", None)
        secret = validated_data.pop("apipay_webhook_secret", None)
        if key is not None:
            department.set_apipay_api_key(key)
        if secret is not None and department.apipay_configured:
            department.set_apipay_webhook_secret(secret)

    def validate_name(self, value):
        name = " ".join(value.split())
        if not name:
            raise serializers.ValidationError("Введите название отдела")
        duplicate = Department.objects.all()
        if self.instance:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if any(
            row.name.strip().casefold() == name.casefold()
            for row in duplicate.only("name")
        ):
            raise serializers.ValidationError(
                "Отдел с таким названием уже существует"
            )
        return name

    def validate_color(self, value):
        value = value.upper()
        if len(value) != 7 or value[0] != "#" or any(
            char not in "0123456789ABCDEF" for char in value[1:]
        ):
            raise serializers.ValidationError("Цвет должен быть в формате #315FD5")
        return value

    @transaction.atomic
    def create(self, validated_data):
        if not Department.objects.exists():
            validated_data["is_default"] = True
        department = Department(code=f"department-{uuid4().hex[:12]}")
        self._apply_apipay(department, validated_data)
        for field, value in validated_data.items():
            setattr(department, field, value)
        department.save()
        if department.is_default:
            Department.objects.exclude(pk=department.pk).update(is_default=False)
        return department

    @transaction.atomic
    def update(self, instance, validated_data):
        will_be_active = validated_data.get("is_active", instance.is_active)
        will_be_default = validated_data.get("is_default", instance.is_default)
        removing_default = instance.is_default and not will_be_default
        if (not will_be_active and instance.is_default) or removing_default:
            replacement = (
                Department.objects.filter(is_active=True)
                .exclude(pk=instance.pk)
                .first()
            )
            if replacement is None:
                field = "is_active" if not will_be_active else "is_default"
                raise serializers.ValidationError(
                    {field: "Сначала создайте или назначьте другой основной отдел"}
                )
            replacement.is_default = True
            replacement.save(update_fields=["is_default"])
            validated_data["is_default"] = False
            will_be_default = False
        if will_be_default and not will_be_active:
            raise serializers.ValidationError(
                {"is_default": "Основной отдел должен быть активным"}
            )
        self._apply_apipay(instance, validated_data)
        department = super().update(instance, validated_data)
        if department.is_default:
            Department.objects.exclude(pk=department.pk).update(is_default=False)
        return department
