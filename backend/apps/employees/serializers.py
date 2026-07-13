from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers
from apps.rbac.models import Permission
from .models import Employee

User = get_user_model()


class EmployeeSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username")
    password = serializers.CharField(write_only=True, required=False, min_length=6)
    role_name = serializers.CharField(source="role.name", read_only=True)
    name = serializers.CharField(read_only=True)
    # Права роли наследуются «вживую»; permissions — личные доступы поверх роли.
    permissions = serializers.SerializerMethodField()
    role_permissions = serializers.SerializerMethodField()
    permission_codes = serializers.SlugRelatedField(
        many=True, write_only=True, required=False,
        source="permissions", slug_field="code",
        queryset=Permission.objects.all(),
    )

    class Meta:
        model = Employee
        fields = ["id", "username", "password", "first_name", "last_name",
                  "phone", "position", "role", "role_name", "name",
                  "permissions", "role_permissions", "permission_codes", "is_active"]

    def get_permissions(self, obj):
        return sorted(p.code for p in obj.permissions.all())

    def get_role_permissions(self, obj):
        if not obj.role_id:
            return []
        return sorted(p.code for p in obj.role.permissions.all())

    def validate(self, attrs):
        if not self.instance and not attrs.get("password"):
            raise serializers.ValidationError(
                {"detail": "Укажите пароль для новой учётной записи", "code": "password_required"})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user_data = validated_data.pop("user")
        password = validated_data.pop("password")
        permissions = validated_data.pop("permissions", None)
        username = user_data["username"]
        if User.objects.filter(username=username).exists():
            raise serializers.ValidationError(
                {"detail": "Пользователь с таким логином уже существует", "code": "username_taken"})
        user = User.objects.create_user(username=username, password=password)
        employee = Employee.objects.create(user=user, **validated_data)
        employee.permissions.set(permissions or [])
        return employee

    @transaction.atomic
    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", None)
        password = validated_data.pop("password", None)
        permissions = validated_data.pop("permissions", None)
        if user_data and user_data.get("username"):
            username = user_data["username"]
            if (User.objects.filter(username=username)
                    .exclude(pk=instance.user_id).exists()):
                raise serializers.ValidationError(
                    {"detail": "Пользователь с таким логином уже существует", "code": "username_taken"})
            instance.user.username = username
            instance.user.save(update_fields=["username"])
        if password:
            instance.user.set_password(password)
            instance.user.save(update_fields=["password"])
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if permissions is not None:
            instance.permissions.set(permissions)
        return instance
