import re

from rest_framework import serializers

# E.164: вместе с кодом страны не длиннее 15 цифр; короче 8 — номер недонабран.
MIN_PHONE_DIGITS = 8
MAX_PHONE_DIGITS = 15


def clean_phone(value: str) -> str:
    """Номер клиента любой страны: формат не навязываем, режем только недонабранный."""
    phone = " ".join(str(value or "").split())
    digits = re.sub(r"\D", "", phone)
    if not MIN_PHONE_DIGITS <= len(digits) <= MAX_PHONE_DIGITS:
        raise serializers.ValidationError("Укажите номер телефона полностью")
    return phone
