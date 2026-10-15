from rest_framework import serializers

from apps.catalog.models import Product
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


class _ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "name"]


class DashboardSettingsSerializer(serializers.ModelSerializer):
    """Чтение — выбранные отделы, склады и товары целиком, запись — их id."""

    departments = serializers.PrimaryKeyRelatedField(many=True, queryset=Department.objects.all())
    warehouses = serializers.PrimaryKeyRelatedField(many=True, queryset=Warehouse.objects.all())
    # Старые клиенты шлют только отделы и склады — выбор товаров они не сбрасывают.
    # Архивный товар выбрать нельзя: в окне «Что показывать» его нет, снять было бы нечем.
    products = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Product.objects.filter(is_active=True), required=False,
    )

    class Meta:
        model = DashboardSettings
        fields = ["departments", "warehouses", "products", "updated_at"]
        read_only_fields = ["updated_at"]

    def to_representation(self, instance):
        return {
            "departments": _DepartmentSerializer(instance.departments.order_by("id"), many=True).data,
            "warehouses": _WarehouseSerializer(instance.warehouses.order_by("id"), many=True).data,
            # Ушедший в архив товар выпадает из выбора: Главная его не фильтрует,
            # а первое же сохранение окна убирает его и из настроек.
            "products": _ProductSerializer(instance.products.filter(is_active=True).order_by("id"), many=True).data,
            "updated_at": instance.updated_at,
        }
