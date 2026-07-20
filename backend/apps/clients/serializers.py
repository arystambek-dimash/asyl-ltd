from rest_framework import serializers
from decimal import Decimal
from uuid import uuid4
from django.db import transaction
from apps.common.money import money_string
from .models import Client, Department, Store


class DepartmentSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True)
    order_count = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = ["id", "code", "name", "color", "is_active", "is_default",
                  "order_count", "created_at"]
        read_only_fields = ["created_at"]

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
        if any(row.name.strip().casefold() == name.casefold() for row in duplicate.only("name")):
            raise serializers.ValidationError("Отдел с таким названием уже существует")
        return name

    def validate_color(self, value):
        value = value.upper()
        if len(value) != 7 or value[0] != "#" or any(
                char not in "0123456789ABCDEF" for char in value[1:]):
            raise serializers.ValidationError("Цвет должен быть в формате #315FD5")
        return value

    @transaction.atomic
    def create(self, validated_data):
        if not Department.objects.exists():
            validated_data["is_default"] = True
        department = Department.objects.create(
            code=f"department-{uuid4().hex[:12]}", **validated_data)
        if department.is_default:
            Department.objects.exclude(pk=department.pk).update(is_default=False)
        return department

    @transaction.atomic
    def update(self, instance, validated_data):
        will_be_active = validated_data.get("is_active", instance.is_active)
        will_be_default = validated_data.get("is_default", instance.is_default)
        removing_default = instance.is_default and not will_be_default
        if (not will_be_active and instance.is_default) or removing_default:
            replacement = Department.objects.filter(is_active=True).exclude(pk=instance.pk).first()
            if replacement is None:
                field = "is_active" if not will_be_active else "is_default"
                raise serializers.ValidationError(
                    {field: "Сначала создайте или назначьте другой основной отдел"})
            replacement.is_default = True
            replacement.save(update_fields=["is_default"])
            validated_data["is_default"] = False
            will_be_default = False
        if will_be_default and not will_be_active:
            raise serializers.ValidationError(
                {"is_default": "Основной отдел должен быть активным"})
        department = super().update(instance, validated_data)
        if department.is_default:
            Department.objects.exclude(pk=department.pk).update(is_default=False)
        return department


class ClientSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)
    debt_total = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = ["id", "first_name", "last_name", "company_name", "phone", "name",
                  "country", "iin", "bank", "bank_account", "user",
                  "debt_total", "created_at"]
        # user связывает клиента с аккаунтом портала (создаётся при регистрации).
        # Запись через staff-API позволила бы перепривязать чужой аккаунт.
        read_only_fields = ["user", "created_at"]

    def get_debt_total(self, obj):
        total = sum((o.remaining_amount for o in obj.orders.all() if o.is_debt),
                    Decimal("0"))
        return money_string(total)

class StoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ["id", "client", "name", "address", "phone",
                  "payment_schedule_type", "payment_days", "contract_signed_at"]

    def validate_client(self, client):
        # Не позволяем привязать магазин к клиенту, недоступному пользователю.
        from .querysets import visible_clients
        request = self.context.get("request")
        if request and not visible_clients(request.user).filter(pk=client.pk).exists():
            raise serializers.ValidationError("Клиент недоступен")
        return client
