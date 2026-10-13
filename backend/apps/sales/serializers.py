from uuid import uuid4

from django.db import transaction
from rest_framework import serializers

from .apipay_accounts import account_with_key
from .models import ApiPayAccount, Department

APIPAY_MIN_KEY_LENGTH = 8


class DepartmentSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True)
    order_count = serializers.SerializerMethodField()
    # Kaspi отдела — выбранное подключение ApiPay (меняется в подключениях);
    # ключи сюда не попадают.
    apipay_configured = serializers.BooleanField(read_only=True)
    apipay_account_name = serializers.CharField(
        source="apipay_account.name", default="", read_only=True
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
            "apipay_account",
            "apipay_account_name",
        ]
        read_only_fields = ["created_at", "apipay_account"]

    def get_order_count(self, obj):
        counts = self.context.get("department_order_counts", {})
        return counts.get(obj.code, 0)

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
        department = super().update(instance, validated_data)
        if department.is_default:
            Department.objects.exclude(pk=department.pk).update(is_default=False)
        return department


class ApiPayAccountSerializer(serializers.ModelSerializer):
    """Подключение ApiPay: ключ и секрет только на запись, наружу — хвост ключа.

    ``departments`` — отделы, которые принимают оплату через это подключение;
    их привязку записывает сервис :mod:`.apipay_accounts`.
    """

    key_hint = serializers.SerializerMethodField()
    webhook_configured = serializers.SerializerMethodField()
    api_key = serializers.CharField(
        write_only=True, required=False, max_length=255, trim_whitespace=True
    )
    webhook_secret = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=255,
        trim_whitespace=True,
    )
    departments = serializers.PrimaryKeyRelatedField(
        many=True, required=False, queryset=Department.objects.all()
    )

    class Meta:
        model = ApiPayAccount
        fields = [
            "id",
            "name",
            "key_hint",
            "webhook_configured",
            "updated_at",
            "departments",
            "api_key",
            "webhook_secret",
        ]
        read_only_fields = ["updated_at"]

    def get_key_hint(self, obj) -> str:
        key = obj.api_key
        return f"••••{key[-4:]}" if key else ""

    def get_webhook_configured(self, obj) -> bool:
        return bool(obj.webhook_secret)

    def validate_name(self, value):
        name = " ".join(value.split())
        if not name:
            raise serializers.ValidationError("Введите название подключения")
        others = ApiPayAccount.objects.all()
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if any(row.name.casefold() == name.casefold() for row in others.only("name")):
            raise serializers.ValidationError("Подключение с таким названием уже есть")
        return name

    def validate_api_key(self, value):
        if len(value) < APIPAY_MIN_KEY_LENGTH:
            raise serializers.ValidationError("Слишком короткий ключ ApiPay")
        if any(char.isspace() for char in value):
            raise serializers.ValidationError("Ключ ApiPay не содержит пробелов")
        duplicate = account_with_key(
            value, exclude_pk=self.instance.pk if self.instance else None
        )
        if duplicate is not None:
            raise serializers.ValidationError(
                f"Этот ключ уже подключён как «{duplicate.name}»"
            )
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get("api_key"):
            raise serializers.ValidationError({"api_key": "Укажите API-ключ ApiPay"})
        return attrs

    def create(self, validated_data):
        account = ApiPayAccount(name=validated_data["name"])
        account.set_api_key(validated_data["api_key"])
        account.set_webhook_secret(validated_data.get("webhook_secret", ""))
        account.save()
        return account

    def update(self, instance, validated_data):
        if "name" in validated_data:
            instance.name = validated_data["name"]
        if "api_key" in validated_data:
            instance.set_api_key(validated_data["api_key"])
        if "webhook_secret" in validated_data:
            instance.set_webhook_secret(validated_data["webhook_secret"])
        instance.save()
        return instance
