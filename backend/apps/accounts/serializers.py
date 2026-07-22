from rest_framework import serializers
from .models import User


class MeSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    client_id = serializers.SerializerMethodField()
    role_name = serializers.SerializerMethodField()
    sales_department = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "is_client", "is_superuser",
                  "permissions", "role_name", "client_id", "sales_department"]

    def get_permissions(self, obj):
        return sorted(obj.perm_codes)

    def get_role_name(self, obj):
        emp = getattr(obj, "employee", None)
        return emp.role.name if emp and emp.role else None

    def get_client_id(self, obj):
        profile = getattr(obj, "client_profile", None)
        return profile.id if profile else None

    def get_sales_department(self, obj):
        employee = getattr(obj, "employee", None)
        department = getattr(employee, "sales_department", None)
        if department is None:
            return None
        return {
            "id": department.id,
            "code": department.code,
            "name": department.name,
            "color": department.color,
        }
