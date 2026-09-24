"""Вход «Вставить отчёт» и «Отправить отчёт» у грузчика и журнала WhatsApp-бота, строки журнала и настройки бота."""
import re
from decimal import Decimal

from rest_framework import serializers

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.clients.phone import clean_phone
from apps.sales.access import scope_by_client_department

from .models import BotMessage, WhatsAppBotSettings
from .wagon_report import DELIVERIES, REPORT_MAX_ORDERS, REPORT_TEXT_MAX_LENGTH

# Как сообщение бота (BotMessage.text): отчёт на 12 вагонов — около 400 символов.
RAIL_REPORT_MAX_LENGTH = 8192


class RailReportSerializer(serializers.Serializer):
    """Текст отчёта и, для «Отгрузить по отчёту», заранее внесённый заказ."""

    text = serializers.CharField(
        max_length=RAIL_REPORT_MAX_LENGTH,
        error_messages={
            "blank": "Вставьте текст отчёта",
            "required": "Вставьте текст отчёта",
            "max_length": "Отчёт слишком длинный — вставьте один отчёт",
        },
    )
    order = serializers.IntegerField(required=False, allow_null=True, min_value=1)


class RailProductCodeSerializer(RailReportSerializer):
    """Код товара из отчёта → товар каталога (catalog.ProductAlias)."""

    code = serializers.CharField(max_length=200)
    product = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.filter(is_active=True),
        error_messages={"does_not_exist": "Товар не найден или в архиве"},
    )


class RailClientNameSerializer(RailReportSerializer):
    """Название клиента в отчёте → клиент и валюта (bots.BotClientProfile)."""

    client_name = serializers.CharField(max_length=300)
    client = serializers.PrimaryKeyRelatedField(
        queryset=Client.objects.all(),
        error_messages={"does_not_exist": "Клиент не найден"},
    )
    currency = serializers.ChoiceField(
        choices=Client.CURRENCIES, error_messages={"invalid_choice": "Выберите валюту: KZT или USD"},
    )

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if request is not None:
            # Клиента чужого отдела не выбрать — как в форме заказа.
            fields["client"].queryset = scope_by_client_department(Client.objects.all(), request.user)
        return fields


class WagonReportComposeSerializer(serializers.Serializer):
    """«Отправить отчёт»: одна отгрузка (``?order=``) или история с фильтрами экрана."""

    order = serializers.IntegerField(required=False, min_value=1)


class WagonReportSendSerializer(serializers.Serializer):
    """Отправить составленный (и, может быть, поправленный) отчёт по отгрузкам истории."""

    order_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), min_length=1, max_length=REPORT_MAX_ORDERS,
        error_messages={"min_length": "Нет отгрузок для отчёта", "empty": "Нет отгрузок для отчёта"},
    )
    text = serializers.CharField(
        max_length=REPORT_TEXT_MAX_LENGTH,
        error_messages={
            "blank": "Текст отчёта пуст",
            "required": "Текст отчёта пуст",
            "max_length": "Отчёт слишком длинный для WhatsApp — выберите период короче",
        },
    )
    # Как экран отправляет: ботом или уже открытой ссылкой WhatsApp.
    delivery = serializers.ChoiceField(choices=DELIVERIES)
    # Ключ нажатия: повтор того же запроса не отправит отчёт второй раз.
    key = serializers.RegexField(r"^[A-Za-z0-9_-]{8,64}$")

    def validate_order_ids(self, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class BotMessageSerializer(serializers.ModelSerializer):
    """Строка журнала WhatsApp-бота."""

    order = serializers.IntegerField(source="order_id", read_only=True, allow_null=True)
    original = serializers.IntegerField(source="original_id", read_only=True, allow_null=True)
    resolved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = BotMessage
        fields = [
            "id", "kind", "status", "chat_id", "chat_name", "sender_id", "sender_name", "text",
            "sent_at", "received_at", "parsed", "issues", "draft", "order", "original",
            "reply", "reply_sent_at", "reply_attempts", "attempts", "error",
            "resolved_by_name", "resolved_at",
        ]
        read_only_fields = fields

    def get_resolved_by_name(self, message) -> str:
        user = message.resolved_by
        return (user.get_full_name() or user.username) if user is not None else ""


# Идентификатор WhatsApp: номер@c.us, группа@g.us или скрытый номер@lid.
_WHATSAPP_ID = re.compile(r"^[0-9]{5,24}(-[0-9]+)?@(c\.us|g\.us|lid)$")
WHATSAPP_ID_LIMIT = 20
# 8 XXX XXX XX XX — номер Казахстана, набранный без кода страны.
_LOCAL_KZ_DIGITS = 11


def whatsapp_phone_digits(value: str) -> str:
    """«+7 701 123-45-67» → «77011234567»: цифры номера с кодом страны, как у Green-API.

    «8 701 123 45 67» (казахстанский номер без кода страны) → «77011234567»:
    Green-API присылает отправителя с кодом 7, и номер с 8 молча не совпал бы
    ни с одним отчётом. С плюсом номер уже международный и не меняется.
    """
    value = "".join(str(value).split())
    digits = re.sub(r"[^0-9]", "", value)
    if not value.startswith("+") and len(digits) == _LOCAL_KZ_DIGITS and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits


def normalize_whatsapp_id(value: str) -> str:
    """«+998 90 111 22 33» → «998901112233@c.us»; готовый идентификатор — как есть."""
    value = "".join(str(value).split()).lower()
    if "@" not in value:
        digits = whatsapp_phone_digits(value)
        value = f"{digits}@c.us" if digits else value
    if not _WHATSAPP_ID.match(value):
        raise serializers.ValidationError(f"«{value}» — не номер WhatsApp и не идентификатор чата")
    return value


class WhatsAppIdListField(serializers.ListField):
    def __init__(self, **kwargs):
        super().__init__(child=serializers.CharField(max_length=128), max_length=WHATSAPP_ID_LIMIT, **kwargs)

    def to_internal_value(self, data):
        # Порядок как ввели, без повторов.
        return list(dict.fromkeys(normalize_whatsapp_id(value) for value in super().to_internal_value(data)))


class WhatsAppBotSettingsSerializer(serializers.ModelSerializer):
    allowed_chat_ids = WhatsAppIdListField(required=False)
    allowed_sender_ids = WhatsAppIdListField(required=False)
    # Дубль вагона — ±дней от даты отчёта (у бота, у грузчика и в журнале).
    duplicate_window_days = serializers.IntegerField(min_value=1, max_value=60, required=False)
    price_tolerance_pct = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=Decimal("0"), max_value=Decimal("100"), required=False,
    )
    # «Отправить отчёт» в истории грузчика: кому и номер WhatsApp (цифры с кодом страны).
    report_recipient_name = serializers.CharField(
        max_length=60, required=False,
        error_messages={"blank": "Укажите, кому отправлять отчёт о вагонах"},
    )
    report_recipient_phone = serializers.CharField(max_length=40, required=False, allow_blank=True)
    seen_chats = serializers.SerializerMethodField()

    class Meta:
        model = WhatsAppBotSettings
        fields = [
            "enabled", "allowed_chat_ids", "allowed_sender_ids", "show_amounts_in_reply",
            "duplicate_window_days", "price_tolerance_pct", "report_recipient_name", "report_recipient_phone",
            "updated_at", "seen_chats",
        ]
        read_only_fields = ["updated_at", "seen_chats"]

    def validate_report_recipient_name(self, value: str) -> str:
        return " ".join(value.split())

    def validate_report_recipient_phone(self, value: str) -> str:
        """Пусто — без номера; иначе номер полностью (правила номеров клиентов) → только цифры."""
        if not value.strip():
            return ""
        clean_phone(value)
        return whatsapp_phone_digits(value)

    def get_seen_chats(self, row) -> list[dict]:
        """Недавние чаты бота — выбрать группу, не зная её идентификатора."""
        return [
            {"id": chat_id, "name": (info or {}).get("name", ""), "at": (info or {}).get("at")}
            for chat_id, info in (row.seen_chats or {}).items()
        ]

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.updated_by = self.context["request"].user
        # Только настройки: состояние номера и опроса пишет процесс бота.
        instance.save(update_fields=list(WhatsAppBotSettings.CONFIG_FIELDS))
        return instance
