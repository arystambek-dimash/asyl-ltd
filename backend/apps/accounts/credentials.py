"""Учётные данные и сессия входа: логин, JWT, refresh-кука.

Общие правила для регистрации клиента в портале, сотрудников и входа.
Пароль проверяется в :mod:`apps.accounts.passwords`.

Сессия входа. Access (15 минут) отдаётся в теле ответа и живёт только в
памяти вкладки. Refresh (30 дней) — только в HttpOnly-куке ``asyl_refresh``
на ``/api/auth/``: скрипт страницы его не прочитает. Обновление, пришедшее,
когда refresh старше часа, ротирует его на новые 30 дней (скользящая сессия:
30 дней простоя — выход), старый уходит в чёрный список. Параллельные вкладки
и потерянный ответ ещё ``REFRESH_REUSE_GRACE_SECONDS`` получают по старому
refresh того же преемника, а не выход. Смена пароля и отключение учётки
отзывают сессию, как и раньше.
"""

import hashlib
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.utils.crypto import constant_time_compare
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import (
    InvalidToken,
    TokenBackendError,
    TokenError,
)
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.state import token_backend
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import BlacklistMixin, RefreshToken
from rest_framework_simplejwt.utils import (
    datetime_from_epoch,
    get_md5_hash_password,
)

User = get_user_model()

REFRESH_COOKIE = "asyl_refresh"
REFRESH_COOKIE_PATH = "/api/auth/"
SESSION_ID_CLAIM = "sid"
# Моложе часа refresh не ротируется: access обновляется каждые 15 минут и на
# каждой загрузке страницы, новая строка в таблицах на каждый раз не нужна.
REFRESH_ROTATE_AFTER = timedelta(hours=1)
# Сколько секунд старый refresh после ротации ещё отдаёт того же преемника.
REFRESH_REUSE_GRACE_SECONDS = 120


def username_taken(username: str, *, exclude_user_id: int | None = None) -> bool:
    users = User.objects.filter(username=username)
    if exclude_user_id is not None:
        users = users.exclude(pk=exclude_user_id)
    return users.exists()


def _session_rejected(detail: str, code: str) -> AuthenticationFailed:
    # Словарь, а не code=: api_exception_handler берёт код из тела ответа.
    return AuthenticationFailed({"detail": detail, "code": code})


def password_change_required() -> AuthenticationFailed:
    return _session_rejected("Смените временный пароль.", "password_change_required")


def _user_by_id(user_id):
    if user_id is None:
        return None
    return User.objects.filter(**{jwt_settings.USER_ID_FIELD: user_id}).first()


def _outstanding_fields(claims: dict, raw: str) -> dict:
    """Поля строки OutstandingToken: вместо самого JWT — только его SHA-256."""
    user_id = claims.get(jwt_settings.USER_ID_CLAIM)
    issued_at = claims.get("iat")
    return {
        "user": lambda: _user_by_id(user_id),
        "token": hashlib.sha256(raw.encode()).hexdigest(),
        "created_at": (
            datetime_from_epoch(issued_at) if issued_at is not None else None
        ),
        "expires_at": datetime_from_epoch(claims["exp"]),
    }


class SessionRefreshToken(RefreshToken):
    """Refresh-токен сессии входа; ``sid`` — id сессии, общий для всех ротаций.

    token_blacklist хранит в OutstandingToken сам JWT: по бэкапу базы или из
    админки его можно было бы предъявить за пользователя на 30 дней. Этот
    класс пишет туда только SHA-256 токена; учёт по ``jti`` тот же.
    """

    @classmethod
    def for_user(cls, user):
        # Мимо BlacklistMixin.for_user: он записал бы в OutstandingToken сам JWT.
        token = super(BlacklistMixin, cls).for_user(user)
        token[SESSION_ID_CLAIM] = uuid4().hex
        token.outstand()
        return token

    def outstand(self) -> OutstandingToken:
        row, _ = OutstandingToken.objects.get_or_create(
            jti=self[jwt_settings.JTI_CLAIM],
            defaults=_outstanding_fields(self.payload, str(self)),
        )
        return row

    def blacklist(self) -> BlacklistedToken:
        row, _ = BlacklistedToken.objects.get_or_create(token=self.outstand())
        return row


def _signed_claims(raw: str) -> dict:
    """Подпись, срок и тип refresh-токена; без ``sid`` — не наш.

    Refresh без ``sid`` выдан до сессий в куке (жил в localStorage): его не
    принимаем, иначе ротация продлила бы его до скользящих 30 дней.

    Чёрный список проверяет конструктор ``SessionRefreshToken`` уже под
    блокировкой строки (:func:`_lock_outstanding`), иначе он не увидел бы
    ротацию, которую параллельный запрос вот-вот закоммитит.
    """
    try:
        claims = token_backend.decode(raw, verify=True)
    except TokenBackendError as exc:
        raise TokenError("Token is invalid or expired") from exc
    if (
        claims.get(jwt_settings.TOKEN_TYPE_CLAIM) != SessionRefreshToken.token_type
        or not claims.get(jwt_settings.JTI_CLAIM)
        or not claims.get(SESSION_ID_CLAIM)
        or "exp" not in claims
    ):
        raise TokenError("Token is invalid or expired")
    return claims


def _lock_outstanding(claims: dict, raw: str) -> None:
    """Строка OutstandingToken этого refresh — под блокировкой до конца транзакции.

    Запросы с одним refresh (вкладки, восстановление сессии браузера) идут по
    одному: второй находит преемника первого, а не гонку за чёрный список
    (IntegrityError → 500) и не второго преемника.
    """
    OutstandingToken.objects.select_for_update().get_or_create(
        jti=claims[jwt_settings.JTI_CLAIM],
        defaults=_outstanding_fields(claims, raw),
    )


def _successor_key(jti: str) -> str:
    return f"accounts:refresh-successor:{jti}"


def _check_session_user(refresh: SessionRefreshToken) -> None:
    """Учётка жива, пароль тот же, временный пароль сменён — иначе 401."""
    user = _user_by_id(refresh.get(jwt_settings.USER_ID_CLAIM))
    if user is None or not jwt_settings.USER_AUTHENTICATION_RULE(user):
        raise _session_rejected(
            "No active account found for the given token.", "no_active_account"
        )
    if user.must_change_password:
        raise password_change_required()
    if jwt_settings.CHECK_REVOKE_TOKEN and not constant_time_compare(
        str(refresh.get(jwt_settings.REVOKE_TOKEN_CLAIM, "")),
        get_md5_hash_password(user.password),
    ):
        raise _session_rejected(
            "The user's password has been changed.", "password_changed"
        )


def _rotate(refresh: SessionRefreshToken) -> None:
    """Старый refresh — в чёрный список, сам объект становится преемником."""
    old_jti = refresh[jwt_settings.JTI_CLAIM]
    refresh.blacklist()
    refresh.set_jti()
    refresh.set_exp()
    refresh.set_iat()
    refresh.outstand()
    # До коммита: запрос, ждущий эту блокировку, должен найти преемника.
    cache.set(_successor_key(old_jti), str(refresh), REFRESH_REUSE_GRACE_SECONDS)


def refresh_session(raw: str | None) -> SessionRefreshToken:
    """Refresh для ответа на ``POST /api/auth/refresh/`` по значению куки.

    Нет куки — 401 ``no_session``; недействительный или отозванный refresh —
    401 ``token_not_valid``; учётка удалена или отключена, пароль сменён или
    требует смены — 401 с кодом причины. Refresh старше часа ротируется.
    """
    if not raw:
        raise _session_rejected("Сессия не найдена: войдите снова.", "no_session")
    try:
        claims = _signed_claims(raw)
        with transaction.atomic():
            _lock_outstanding(claims, raw)
            successor = cache.get(_successor_key(claims[jwt_settings.JTI_CLAIM]))
            refresh = SessionRefreshToken(successor or raw)
            _check_session_user(refresh)
            issued_at = datetime_from_epoch(refresh.get("iat", 0))
            if (
                successor is None
                and refresh.current_time - issued_at >= REFRESH_ROTATE_AFTER
            ):
                _rotate(refresh)
    except TokenError as exc:
        raise InvalidToken(exc.args[0]) from exc
    return refresh


def end_session(raw: str | None) -> None:
    """Выход: refresh из куки и его свежий преемник — в чёрный список.

    Пустая, чужая или уже отозванная кука — ничего не делать: выход идемпотентен.
    """
    if not raw:
        return
    try:
        claims = _signed_claims(raw)
    except TokenError:
        return
    key = _successor_key(claims[jwt_settings.JTI_CLAIM])
    with transaction.atomic():
        _lock_outstanding(claims, raw)
        for value in (raw, cache.get(key)):
            if not value:
                continue
            try:
                SessionRefreshToken(value).blacklist()
            except TokenError:
                pass  # уже в чёрном списке или истёк
        cache.delete(key)


def _write_refresh_cookie(response, value: str, max_age: int) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        value,
        max_age=max_age,
        path=REFRESH_COOKIE_PATH,
        secure=settings.AUTH_REFRESH_COOKIE_SECURE,
        httponly=True,
        samesite="Strict",
    )


def clear_refresh_cookie(response) -> None:
    """Снять refresh-куку — с теми же Path, SameSite и Secure, что при выдаче."""
    _write_refresh_cookie(response, "", 0)


def session_response(refresh: SessionRefreshToken, *, status: int = 200) -> Response:
    """Ответ входа и обновления: refresh — в HttpOnly-куку, в теле только access."""
    response = Response({"access": str(refresh.access_token)}, status=status)
    response["Cache-Control"] = "no-store"
    remaining = datetime_from_epoch(refresh["exp"]) - refresh.current_time
    _write_refresh_cookie(
        response, str(refresh), max(0, int(remaining.total_seconds()))
    )
    return response


def start_session(user, *, status: int = 200) -> Response:
    """Новая сессия входа: логин, смена временного пароля, регистрация."""
    return session_response(SessionRefreshToken.for_user(user), status=status)


def _origin_trusted(request) -> bool:
    origin = request.headers.get("Origin", "")
    return (
        bool(origin)
        and origin != "null"
        and (
            origin == f"{request.scheme}://{request.get_host()}"
            or origin in settings.AUTH_TRUSTED_ORIGINS
        )
    )


class SessionEndpointMixin:
    """Эндпоинт, который ставит или читает refresh-куку: только JSON и свой Origin.

    DRF-вьюхи освобождены от CSRF, а браузер сам шлёт куку и принимает
    Set-Cookie. Без проверки чужая страница формой подменила бы сессию (вход
    под учёткой злоумышленника) или дёргала бы ротацию и выход.
    """

    parser_classes = [JSONParser]

    def initial(self, request, *args, **kwargs):
        # Сначала троттл: запросы с чужим Origin тоже расходуют лимит.
        super().initial(request, *args, **kwargs)
        if not _origin_trusted(request):
            raise PermissionDenied(
                {"detail": "Запрос пришёл не со страницы сайта.", "code": "bad_origin"}
            )
