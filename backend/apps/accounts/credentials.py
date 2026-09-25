"""Учётные данные новых и существующих пользователей: логин и JWT.

Общие правила для регистрации клиента в портале, сотрудников и входа.
Пароль проверяется в :mod:`apps.accounts.passwords`.
"""

from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


def username_taken(username: str, *, exclude_user_id: int | None = None) -> bool:
    users = User.objects.filter(username=username)
    if exclude_user_id is not None:
        users = users.exclude(pk=exclude_user_id)
    return users.exists()


def token_pair(user) -> dict[str, str]:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}
