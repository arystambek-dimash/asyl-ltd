"""Симметричное шифрование секретов, хранимых в базе (ключи ApiPay отделов).

Ключ Fernet выводится из ``SECRET_KEY``: отдельного секрета в окружении не
нужно, а production-настройки уже требуют сильный ``SECRET_KEY``.
``SECRET_KEY_FALLBACKS`` даёт ротацию без потери старых токенов: новые
значения шифруются первым ключом, старые читаются любым из списка.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings

_DERIVATION_PREFIX = "asyl-secret-store:"


class SecretDecryptError(ValueError):
    """Токен не расшифровывается текущим SECRET_KEY и его fallback-ключами."""


def _fernet_key(secret_key: str) -> bytes:
    digest = hashlib.sha256(
        (_DERIVATION_PREFIX + secret_key).encode("utf-8")
    ).digest()
    return base64.urlsafe_b64encode(digest)


def _cipher() -> MultiFernet:
    keys = [settings.SECRET_KEY, *getattr(settings, "SECRET_KEY_FALLBACKS", [])]
    return MultiFernet([Fernet(_fernet_key(key)) for key in keys if key])


def encrypt_secret(value: str) -> str:
    """Пустая строка остаётся пустой: «ключ не задан» хранится как ``""``."""
    if not value:
        return ""
    return _cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _cipher().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise SecretDecryptError("secret token is not readable") from exc
