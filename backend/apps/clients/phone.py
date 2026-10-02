import re

from rest_framework import serializers

# E.164: вместе с кодом страны не длиннее 15 цифр; короче 8 — номер недонабран.
MIN_PHONE_DIGITS = 8
MAX_PHONE_DIGITS = 15


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def clean_phone(value: str) -> str:
    """Номер клиента любой страны: формат не навязываем, режем только недонабранный."""
    phone = " ".join(str(value or "").split())
    if not MIN_PHONE_DIGITS <= len(_digits(phone)) <= MAX_PHONE_DIGITS:
        raise serializers.ValidationError("Укажите номер телефона полностью")
    return phone


def kz_local_phone(value) -> str:
    """Казахстанский номер записью 8XXXXXXXXXX (+7 702… → 8702…); не такой номер — пусто.

    Так его требует Kaspi (``apps.orders.apipay.normalize_phone``) и так его пишут в чатах.
    """
    digits = _digits(value)
    if len(digits) == 11 and digits[0] in ("7", "8"):
        return "8" + digits[1:]
    if len(digits) == 10:
        return "8" + digits
    return ""


def chat_phone(value) -> str:
    """Телефон для чатов: +7 — 8XXXXXXXXXX, другие страны — «+» и цифры; пусто — пусто."""
    digits = _digits(value)
    if not digits:
        return ""
    foreign = str(value).strip().startswith("+") and not digits.startswith("7")
    return ("" if foreign else kz_local_phone(digits)) or f"+{digits}"
