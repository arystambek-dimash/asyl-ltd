from typing import NoReturn

from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainSerializer

from apps.sales.access import assigned_department_id

from .credentials import password_change_required
from .models import User
from .passwords import validate_new_password


class LoginSerializer(TokenObtainSerializer):
    """Логин и пароль; сессию выдаёт ``credentials.start_session``."""

    def validate(self, attrs):
        data = super().validate(attrs)
        if self.user.is_client and self.user.must_change_password:
            raise password_change_required()
        return data


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
            validate_new_password(new_password, user=user)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({"new_password": exc.detail}) from exc

        user.set_password(new_password)
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        return user


class MeSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    position = serializers.SerializerMethodField()
    sales_department = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "username", "first_name", "last_name",
            "is_client", "is_superuser",
            "permissions", "position", "sales_department"]


    def get_permissions(self, obj):
        return sorted(obj.perm_codes)

    def get_position(self, obj):
        emp = getattr(obj, "employee", None)
        return emp.position if emp else None

    def get_sales_department(self, obj):
        """Отдел, которым сервер ограничивает пользователя (sales.access); у суперюзера — нет."""
        if assigned_department_id(obj) is None:
            return None
        department = obj.employee.sales_department
        return {
            "id": department.id,
            "code": department.code,
            "name": department.name,
            "color": department.color,
        }
