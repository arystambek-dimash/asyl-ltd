"""Проверка нового пароля настроенными правилами Django для API."""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers


def validate_new_password(value, *, user=None, username="", first_name="", last_name=""):
    """Вернуть пароль или поднять ``serializers.ValidationError`` со списком причин.

    Без ``user`` сверка похожести идёт с временным пользователем из логина и
    имени: учётки ещё нет, а настоящую валидация трогать не должна.
    """
    if user is None:
        user = get_user_model()(
            username=str(username or ""),
            first_name=str(first_name or ""),
            last_name=str(last_name or ""),
        )
    try:
        validate_password(value, user=user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(exc.messages) from exc
    return value
