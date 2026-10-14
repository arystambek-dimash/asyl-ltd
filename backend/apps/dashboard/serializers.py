from rest_framework import serializers

from apps.sales.models import Department
from apps.warehouse.models import Warehouse

from .models import DashboardSettings


class _DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "code", "name"]


class _WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = ["id", "name"]


class DashboardSettingsSerializer(serializers.ModelSerializer):
    """Чтение — выбранные отделы и склады целиком, запись — их id."""

    departments = serializers.PrimaryKeyRelatedField(many=True, queryset=Department.objects.all())
    warehouses = serializers.PrimaryKeyRelatedField(many=True, queryset=Warehouse.objects.all())

    class Meta:
        model = DashboardSettings
        fields = ["departments", "warehouses", "updated_at"]
        read_only_fields = ["updated_at"]

    def to_representation(self, instance):
        return {
            "departments": _DepartmentSerializer(instance.departments.order_by("id"), many=True).data,
            "warehouses": _WarehouseSerializer(instance.warehouses.order_by("id"), many=True).data,
            "updated_at": instance.updated_at,
        }
