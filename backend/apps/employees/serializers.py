from collections.abc import Mapping

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from apps.sales.models import Department
from apps.sys_permissions.models import Permission

from .models import Employee

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


def _validate_user_password(value, *, user, initial_data):
    candidate = user
    candidate.username = str(initial_data.get("username", candidate.username or ""))
    candidate.first_name = str(
        initial_data.get("first_name", candidate.first_name or "")
    )
    candidate.last_name = str(
        initial_data.get("last_name", candidate.last_name or "")
    )
    try:
        validate_password(value, user=candidate)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(exc.messages)
    return value


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
        ]

    def get_permissions(self, obj):
        return sorted(permission.code for permission in obj.permissions.all())


class EmployeeCreateUpdateSerializer(serializers.ModelSerializer):
    username = serializers.CharField(write_only=True, required=False)
    password = serializers.CharField(write_only=True, required=False)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    permission_codes = _permission_codes_field()
    sales_department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        allow_null=True,
        required=False,
    )

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
        if value is None or value.is_active:
            return value
        if self.instance is not None and self.instance.sales_department_id == value.pk:
            return value
        raise serializers.ValidationError("Выберите действующий отдел продаж")

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Пользователь с таким логином уже существует")
        return value

    def validate_password(self, value):
        return _validate_user_password(
            value,
            user=User(),
            initial_data=self.initial_data,
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
        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            is_active=validated_data.get("is_active", True),
        )
        employee = Employee.objects.create(user=user, **validated_data)
        employee.permissions.set(permissions)
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
    sales_department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = Employee
        fields = [
            "username",
            "permission_codes",
            "is_active",
            "sales_department",
        ]

    def validate_sales_department(self, value):
        if value is None or value.is_active:
            return value
        if self.instance.sales_department_id == value.pk:
            return value
        raise serializers.ValidationError("Выберите действующий отдел продаж")

    def validate(self, attrs):
        user_data = attrs.get("user")
        if user_data:
            username = user_data.get("username")
            if (
                username
                and User.objects.filter(username=username)
                .exclude(pk=self.instance.user_id)
                .exists()
            ):
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
            if instance.user.is_active != instance.is_active:
                instance.user.is_active = instance.is_active
                instance.user.save(update_fields=["is_active"])

        if employee_update_fields:
            instance.save(update_fields=employee_update_fields)

        if permissions is not None:
            instance.permissions.set(permissions)
        return instance


class EmployeePasswordSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        employee = self.context["employee"]
        return _validate_user_password(
            value,
            user=employee.user,
            initial_data=self.initial_data,
        )

    def save(self, **kwargs):
        employee = self.context["employee"]
        employee.user.set_password(self.validated_data["password"])
        employee.user.save(update_fields=["password"])
        return employee
