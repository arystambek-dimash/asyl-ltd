from rest_framework import serializers
from .models import User


class MeSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    client_id = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "is_client", "is_superuser", "roles", "client_id"]

    def get_roles(self, obj):
        roles = []
        for name in ("manager", "accountant", "operator", "boss"):
            if obj._in_group(name):
                roles.append(name)
        return roles

    def get_client_id(self, obj):
        profile = getattr(obj, "client_profile", None)
        return profile.id if profile else None
