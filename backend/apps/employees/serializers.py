from collections.abc import Mapping

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from apps.accounts.credentials import username_taken
from apps.accounts.passwords import validate_new_password
from apps.catalog.models import Product
from apps.common.money import money_string
from apps.orders.debt import order_remaining
from apps.orders.models import Order, OrderItem
from apps.orders.statuses import is_financial, public_status_label
from apps.portal.serializers import MAX_PORTAL_ITEM_QUANTITY, MAX_PORTAL_ORDER_ITEMS
from apps.sales.models import Department
from apps.sys_permissions.models import Permission
from apps.warehouse.models import Warehouse

from .models import Employee
from .shop import cancel_blocker, follow_employee_department, set_employee_shop, shop_settings

User = get_user_model()


def _permission_codes_field():
    return serializers.SlugRelatedField(
        many=True,
        write_only=True,
        required=False,
        source="permissions",
        slug_field="code",
        queryset=Permission.objects.all(),
    )


def _sales_department_field():
    return serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        allow_null=True,
        required=False,
    )


def _validate_active_department(value, instance=None):
    """Новый отдел — только действующий; уже закреплённый можно оставить."""
    if value is None or value.is_active:
        return value
    if instance is not None and instance.sales_department_id == value.pk:
        return value
    raise serializers.ValidationError("Выберите действующий отдел продаж")


def _validate_permission_assignment(serializer, attrs):
    permissions = attrs.get("permissions", serializers.empty)
    if permissions is serializers.empty:
        return

    request = serializer.context.get("request")
    actor = getattr(request, "user", None)
    if actor is None or not actor.is_authenticated:
        raise serializers.ValidationError(
            {"detail": "Не удалось определить пользователя", "code": "actor_required"}
        )
    if actor.is_superuser:
        return

    current_codes = set()
    if serializer.instance is not None:
        current_codes = set(
            serializer.instance.permissions.values_list("code", flat=True)
        )
    requested_codes = {permission.code for permission in permissions}
    excess = sorted(requested_codes - current_codes - actor.perm_codes)
    if excess:
        raise serializers.ValidationError(
            {
                "detail": "Нельзя выдать права, которых нет у вас: "
                + ", ".join(excess),
                "code": "perm_escalation",
            }
        )


class ShopSettingsSerializer(serializers.Serializer):
    """«Свой магазин» в карточке сотрудника (``shop.set_employee_shop``)."""

    enabled = serializers.BooleanField()
    name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    warehouse = serializers.PrimaryKeyRelatedField(
        queryset=Warehouse.objects.all(), required=False, allow_null=True, default=None,
    )


def _save_shop(serializer, employee, shop_data) -> None:
    if shop_data is None:
        return
    shop = set_employee_shop(employee, serializer.context["request"].user, **shop_data)
    if shop is not None:
        # Ответ читает employee.shop: без этого он показал бы прежний магазин
        # (или его отсутствие) из кэша связи.
        employee.shop = shop


class EmployeeReadSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    sales_department_name = serializers.CharField(
        source="sales_department.name", read_only=True, allow_null=True
    )
    sales_department_color = serializers.CharField(
        source="sales_department.color", read_only=True, allow_null=True
    )
    name = serializers.CharField(read_only=True)
    permissions = serializers.SerializerMethodField()
    shop = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "phone",
            "position",
            "sales_department",
            "sales_department_name",
            "sales_department_color",
            "name",
            "permissions",
            "is_active",
            "shop",
        ]

    def get_permissions(self, obj):
        return sorted(permission.code for permission in obj.permissions.all())

    def get_shop(self, obj):
        return shop_settings(getattr(obj, "shop", None))


class EmployeeCreateUpdateSerializer(serializers.ModelSerializer):
    username = serializers.CharField(write_only=True, required=False)
    password = serializers.CharField(write_only=True, required=False)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    permission_codes = _permission_codes_field()
    sales_department = _sales_department_field()
    shop = ShopSettingsSerializer(write_only=True, required=False)

    class Meta:
        model = Employee
        fields = [
            "username",
            "password",
            "first_name",
            "last_name",
            "phone",
            "position",
            "sales_department",
            "permission_codes",
            "is_active",
            "shop",
        ]

    def to_representation(self, instance):
        return EmployeeReadSerializer(instance, context=self.context).data

    def to_internal_value(self, data):
        if self.instance is not None and isinstance(data, Mapping):
            security_fields = {
                "username",
                "password",
                "permission_codes",
                "is_active",
                "sales_department",
                "shop",
            } & set(data)
            if security_fields:
                raise serializers.ValidationError(
                    {
                        field: "Используйте отдельный endpoint безопасности."
                        for field in sorted(security_fields)
                    }
                )
        return super().to_internal_value(data)

    def validate_sales_department(self, value):
        return _validate_active_department(value)

    def validate_username(self, value):
        if username_taken(value):
            raise serializers.ValidationError("Пользователь с таким логином уже существует")
        return value

    def validate_password(self, value):
        return validate_new_password(
            value,
            username=self.initial_data.get("username"),
            first_name=self.initial_data.get("first_name"),
            last_name=self.initial_data.get("last_name"),
        )

    def validate(self, attrs):
        if self.instance is None:
            missing = {
                field: "Обязательное поле."
                for field in ("username", "password")
                if field not in attrs
            }
            if missing:
                raise serializers.ValidationError(missing)
            _validate_permission_assignment(self, attrs)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        username = validated_data.pop("username")
        password = validated_data.pop("password")
        first_name = validated_data.pop("first_name")
        last_name = validated_data.pop("last_name")
        permissions = validated_data.pop("permissions", [])
        shop_data = validated_data.pop("shop", None)
        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            is_active=validated_data.get("is_active", True),
        )
        employee = Employee.objects.create(user=user, **validated_data)
        employee.permissions.set(permissions)
        _save_shop(self, employee, shop_data)
        return employee

    @transaction.atomic
    def update(self, instance, validated_data):
        user_update_fields = []
        for field in ("first_name", "last_name"):
            if field not in validated_data:
                continue
            value = validated_data.pop(field)
            if getattr(instance.user, field) != value:
                setattr(instance.user, field, value)
                user_update_fields.append(field)

        instance = super().update(instance, validated_data)
        if user_update_fields:
            instance.user.save(update_fields=user_update_fields)
        return instance


class EmployeeSecuritySerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", required=False)
    permission_codes = _permission_codes_field()
    sales_department = _sales_department_field()
    shop = ShopSettingsSerializer(write_only=True, required=False)

    class Meta:
        model = Employee
        fields = [
            "username",
            "permission_codes",
            "is_active",
            "sales_department",
            "shop",
        ]

    def validate_sales_department(self, value):
        return _validate_active_department(value, self.instance)

    def validate(self, attrs):
        user_data = attrs.get("user")
        if user_data:
            username = user_data.get("username")
            if username and username_taken(username, exclude_user_id=self.instance.user_id):
                raise serializers.ValidationError(
                    {"username": "Пользователь с таким логином уже существует"}
                )
        _validate_permission_assignment(self, attrs)
        return attrs

    def to_representation(self, instance):
        return EmployeeReadSerializer(instance, context=self.context).data

    @transaction.atomic
    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", None)
        permissions = validated_data.pop("permissions", None)
        shop_data = validated_data.pop("shop", None)

        if user_data and user_data.get("username"):
            instance.user.username = user_data["username"]
            instance.user.save(update_fields=["username"])

        employee_update_fields = []
        if "sales_department" in validated_data:
            instance.sales_department = validated_data["sales_department"]
            employee_update_fields.append("sales_department")

        if "is_active" in validated_data:
            instance.is_active = validated_data["is_active"]
            employee_update_fields.append("is_active")

        if employee_update_fields:
            instance.save(update_fields=employee_update_fields)

        if permissions is not None:
            instance.permissions.set(permissions)
        if shop_data is None and "sales_department" in employee_update_fields:
            follow_employee_department(instance, self.context["request"].user)
        _save_shop(self, instance, shop_data)
        return instance


class EmployeePasswordSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        user = self.context["employee"].user
        return validate_new_password(
            value,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
        )

    def save(self, **kwargs):
        employee = self.context["employee"]
        employee.user.set_password(self.validated_data["password"])
        employee.user.save(update_fields=["password"])
        return employee


# «Мой магазин» сотрудника: заказ сразу со склада и свои заказы (employees/shop.py).


class ShopOrderItemInputSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_PORTAL_ITEM_QUANTITY)


class ShopOrderCreateSerializer(serializers.Serializer):
    # Те же пределы, что у корзины портала: корзина на фронте общая.
    items = ShopOrderItemInputSerializer(many=True, allow_empty=False, max_length=MAX_PORTAL_ORDER_ITEMS)


class ShopOrderItemSerializer(serializers.ModelSerializer):
    product_label = serializers.CharField(read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "product", "product_label", "quantity", "unit_price", "is_bonus"]


class ShopOrderSerializer(serializers.ModelSerializer):
    items = ShopOrderItemSerializer(many=True, read_only=True)
    bags = serializers.IntegerField(source="goods_bags", read_only=True)
    status_label = serializers.SerializerMethodField()
    shipped_at = serializers.SerializerMethodField()
    total_amount = serializers.SerializerMethodField()
    paid_total = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    cancel_blocker = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id", "created_at", "shipped_at", "status", "status_label", "currency",
            "items", "bags", "total_amount", "paid_total", "remaining_amount",
            "payment_status", "cancel_blocker",
        ]

    def get_status_label(self, obj):
        return public_status_label(obj.status)

    def get_shipped_at(self, obj):
        shipment = getattr(obj, "shipment", None)
        return shipment.shipped_at if shipment else None

    # Отменённый заказ ничего не стоит — деньги только у финансовых (как в портале).
    def get_total_amount(self, obj):
        return money_string(obj.total_amount) if is_financial(obj.status) else None

    def get_paid_total(self, obj):
        return money_string(obj.paid_total) if is_financial(obj.status) else None

    def get_remaining_amount(self, obj):
        return money_string(order_remaining(obj)) if is_financial(obj.status) else None

    def get_cancel_blocker(self, obj):
        """Пусто — сотрудник может сам отменить заказ (час после списания, без денег)."""
        return cancel_blocker(obj, self.context["request"].user)
