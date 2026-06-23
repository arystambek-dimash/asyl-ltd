from rest_framework import serializers
from .models import Permission, Role


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "code", "section", "action", "label"]


class RoleSerializer(serializers.ModelSerializer):
    permission_codes = serializers.SlugRelatedField(
        many=True,
        write_only=True,
        required=False,
        source="permissions",
        slug_field="code",
        queryset=Permission.objects.all(),
    )
    permissions = PermissionSerializer(many=True, read_only=True)
    employee_count = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ["id", "name", "description", "is_system",
                  "permissions", "permission_codes", "employee_count"]
        read_only_fields = ["is_system"]

    def get_employee_count(self, obj):
        return getattr(obj, "employee_count", obj.employees.count())

    def create(self, validated_data):
        permissions = validated_data.pop("permissions", [])
        role = Role.objects.create(**validated_data)
        role.permissions.set(permissions)
        return role

    def update(self, instance, validated_data):
        permissions = validated_data.pop("permissions", None)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.save()
        if permissions is not None:
            instance.permissions.set(permissions)
        return instance
