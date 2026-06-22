from rest_framework import serializers
from .models import Permission, Role


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "code", "section", "action", "label"]


class RoleSerializer(serializers.ModelSerializer):
    permission_codes = serializers.ListField(
        child=serializers.CharField(), write_only=True, required=False)
    permissions = PermissionSerializer(many=True, read_only=True)
    employee_count = serializers.IntegerField(source="employees.count", read_only=True)

    class Meta:
        model = Role
        fields = ["id", "name", "description", "is_system",
                  "permissions", "permission_codes", "employee_count"]
        read_only_fields = ["is_system"]

    def _apply_codes(self, role, codes):
        perms = Permission.objects.filter(code__in=codes)
        role.permissions.set(perms)

    def create(self, validated_data):
        codes = validated_data.pop("permission_codes", [])
        role = Role.objects.create(**validated_data)
        self._apply_codes(role, codes)
        return role

    def update(self, instance, validated_data):
        codes = validated_data.pop("permission_codes", None)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.save()
        if codes is not None:
            self._apply_codes(instance, codes)
        return instance
