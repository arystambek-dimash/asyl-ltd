from rest_framework import serializers
from .models import User


class MeSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    client_id = serializers.SerializerMethodField()
    role_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "is_client", "is_superuser",
                  "permissions", "role_name", "client_id"]

    def get_permissions(self, obj):
        return sorted(obj.perm_codes)

    def get_role_name(self, obj):
        emp = getattr(obj, "employee", None)
        return emp.role.name if emp and emp.role else None

    def get_client_id(self, obj):
        profile = getattr(obj, "client_profile", None)
        return profile.id if profile else None
