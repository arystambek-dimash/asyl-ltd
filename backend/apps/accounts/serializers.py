from django.contrib.auth import get_user_model
from django.utils.crypto import constant_time_compare
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.utils import get_md5_hash_password

from .models import User


class RevocableTokenRefreshSerializer(TokenRefreshSerializer):
    """Reject stale refresh tokens instead of minting unusable access tokens."""

    def validate(self, attrs):
        refresh = self.token_class(attrs["refresh"])
        user_id = refresh.payload.get(jwt_settings.USER_ID_CLAIM)
        user = (
            get_user_model()
            .objects.filter(**{jwt_settings.USER_ID_FIELD: user_id})
            .first()
            if user_id is not None
            else None
        )
        if user is None or not jwt_settings.USER_AUTHENTICATION_RULE(user):
            raise AuthenticationFailed(
                self.error_messages["no_active_account"],
                "no_active_account",
            )
        if jwt_settings.CHECK_REVOKE_TOKEN and not constant_time_compare(
            str(refresh.get(jwt_settings.REVOKE_TOKEN_CLAIM, "")),
            get_md5_hash_password(user.password),
        ):
            raise AuthenticationFailed(
                "The user's password has been changed.",
                "password_changed",
            )
        return super().validate(attrs)


class MeSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    client_id = serializers.SerializerMethodField()
    role_name = serializers.SerializerMethodField()
    sales_department = serializers.SerializerMethodField()
    is_monoblock = serializers.SerializerMethodField()
    monoblock_name = serializers.SerializerMethodField()
    monoblock_camera = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "is_client", "is_superuser",
                  "is_monoblock", "monoblock_name", "monoblock_camera",
                  "permissions", "role_name", "client_id", "sales_department"]

    def get_is_monoblock(self, obj):
        return obj.is_monoblock

    def get_monoblock_name(self, obj):
        device = obj.active_monoblock_device
        return device.name if device else None

    def get_monoblock_camera(self, obj):
        device = obj.active_monoblock_device
        return device.camera_source if device else None

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
