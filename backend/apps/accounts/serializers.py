from typing import NoReturn

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils.crypto import constant_time_compare
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.utils import get_md5_hash_password

from .models import User


def _password_change_required():
    return AuthenticationFailed(
        {
            "detail": "Смените временный пароль.",
            "code": "password_change_required",
        }
    )


class PasswordChangeAwareTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        if (
            self.user.is_active
            and self.user.is_client
            and self.user.must_change_password
        ):
            raise _password_change_required()
        return data


class RevocableTokenRefreshSerializer(TokenRefreshSerializer):
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
        if user.must_change_password:
            raise _password_change_required()
        if jwt_settings.CHECK_REVOKE_TOKEN and not constant_time_compare(
            str(refresh.get(jwt_settings.REVOKE_TOKEN_CLAIM, "")),
            get_md5_hash_password(user.password),
        ):
            raise AuthenticationFailed(
                "The user's password has been changed.",
                "password_changed",
            )
        return super().validate(attrs)


class InitialPasswordSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    current_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )
    new_password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )

    @staticmethod
    def _invalid_credentials() -> NoReturn:
        raise AuthenticationFailed(
            {
                "detail": "Неверный логин или временный пароль.",
                "code": "invalid_credentials",
            }
        )

    @transaction.atomic
    def create(self, validated_data):
        username = validated_data["username"]
        current_password = validated_data["current_password"]
        new_password = validated_data["new_password"]

        user = (
            User.objects.select_for_update()
            .filter(username=username)
            .first()
        )
        if user is None:
            # Match Django's authentication timing for an unknown username.
            User().set_password(current_password)
            self._invalid_credentials()

        if not (
            user.check_password(current_password)
            and user.is_active
            and user.is_client
        ):
            self._invalid_credentials()
        if not user.must_change_password:
            raise serializers.ValidationError(
                {
                    "detail": "Временный пароль уже был заменён.",
                    "code": "password_change_not_required",
                }
            )
        if user.check_password(new_password):
            raise serializers.ValidationError(
                {"new_password": "Новый пароль должен отличаться от временного."}
            )

        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"new_password": exc.messages}
            ) from exc

        user.set_password(new_password)
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        return user


class MeSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    client_id = serializers.SerializerMethodField()
    position = serializers.SerializerMethodField()
    sales_department = serializers.SerializerMethodField()
    is_monoblock = serializers.SerializerMethodField()
    monoblock_name = serializers.SerializerMethodField()
    monoblock_camera = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "username", "first_name", "last_name",
            "is_client", "is_superuser",
            "is_monoblock", "monoblock_name", "monoblock_camera",
            "permissions", "position", "client_id", "sales_department"]

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

    def get_position(self, obj):
        emp = getattr(obj, "employee", None)
        return emp.position if emp else None

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
