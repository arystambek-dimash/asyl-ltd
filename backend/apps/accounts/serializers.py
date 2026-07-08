from rest_framework import serializers
from .models import User


class MeSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    client_id = serializers.SerializerMethodField()
    role_name = serializers.SerializerMethodField()
    department_names = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "is_client", "is_superuser",
                  "permissions", "role_name", "client_id", "department_names"]

    def get_department_names(self, obj):
        # Названия отделов редактируются админом — фронт берёт их отсюда.
        from apps.clients.models import Department
        return {d.code: d.name for d in Department.objects.all()}

    def get_permissions(self, obj):
        return sorted(obj.perm_codes)

    def get_role_name(self, obj):
        emp = getattr(obj, "employee", None)
        return emp.role.name if emp and emp.role else None

    def get_client_id(self, obj):
        profile = getattr(obj, "client_profile", None)
        return profile.id if profile else None
