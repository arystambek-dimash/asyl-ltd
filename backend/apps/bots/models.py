from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.clients.models import Client
from apps.common.text import match_key

# Дубль вагона: тот же номер в живом заказе с датой отгрузки в пределах ±N
# дней от даты отчёта. Повторно присланный отчёт — той же датой, а раньше чем
# через 3 дня вагон под погрузку физически не вернётся (решение владельца).
DEFAULT_DUPLICATE_WINDOW_DAYS = 3


class BotClientProfile(models.Model):
    """Как отчёт о вагонах называет клиента и в какой валюте ему считать.

    «ООО OSIYO NAV NIHOL» в шапке отчёта → клиент CRM и валюта цены. Профиль
    нужен, когда название в отчёте не совпадает с карточкой клиента или валюта
    отгрузки отличается от валюты клиента по умолчанию. Пополняется при
    разборе отчёта (бот и «Вставить отчёт» у грузчика).
    """

    # Название как в отчёте — для людей; сравнение — по ключу.
    name = models.CharField(max_length=200)
    # apps.common.text.match_key(name): «ООО OSIYO» и «OOO "Osiyo"» — одно.
    name_key = models.CharField(max_length=200, unique=True)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="bot_profiles")
    currency = models.CharField(max_length=3, choices=Client.CURRENCIES)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="bot_client_profiles",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.name = " ".join(self.name.split())
        self.name_key = match_key(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} → {self.client} ({self.currency})"


class BotMessage(models.Model):
    """Сообщение из чата бота: отчёт о вагонах, правка или удаление отчёта.

    Одна строка на сообщение провайдера — повторная доставка того же
    уведомления (очередь Green-API отдаёт его, пока бот не подтвердит приём)
    ничего не проводит второй раз. ``status`` — что с сообщением сделали:
    провели сами, отправили человеку на разбор или пропустили.
    """

    PROVIDER_GREEN_API = "green_api"

    RECEIVED = "received"
    PARSED = "parsed"
    APPLIED = "applied"
    NEEDS_REVIEW = "needs_review"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    REJECTED = "rejected"
    IGNORED = "ignored"
    FAILED = "failed"
    STATUSES = [
        (RECEIVED, "Получено"),
        (PARSED, "Разобрано"),
        (APPLIED, "Проведено"),
        (NEEDS_REVIEW, "На проверке"),
        # Черновик ИИ по неразобранному сообщению — проводит только человек.
        (AWAITING_CONFIRMATION, "Черновик на проверке"),
        (REJECTED, "Отклонено"),
        (IGNORED, "Пропущено"),
        (FAILED, "Ошибка"),
    ]
    # Ждут человека в журнале.
    REVIEW_STATUSES = (NEEDS_REVIEW, AWAITING_CONFIRMATION, FAILED, REJECTED)

    MESSAGE = "message"
    EDITED = "edited"
    DELETED = "deleted"
    KINDS = [(MESSAGE, "Сообщение"), (EDITED, "Изменено"), (DELETED, "Удалено")]

    provider = models.CharField(max_length=20, default=PROVIDER_GREEN_API)
    provider_message_id = models.CharField(max_length=160)
    chat_id = models.CharField(max_length=128)
    chat_name = models.CharField(max_length=200, blank=True, default="")
    sender_id = models.CharField(max_length=128, blank=True, default="")
    sender_name = models.CharField(max_length=200, blank=True, default="")
    kind = models.CharField(max_length=10, choices=KINDS, default=MESSAGE)
    # Правка или удаление — исходное сообщение, если бот его видел.
    original = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="revisions",
    )
    # Не длиннее 8 КБ (``apps.bots.serializers.RAIL_REPORT_MAX_LENGTH``).
    text = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=24, choices=STATUSES, default=RECEIVED)
    # Итог разбора для журнала: день, клиент, станция, вагоны, тонны, мешки.
    parsed = models.JSONField(default=dict, blank=True)
    # Причины разбора: [{code, message, line, subject, order_id}].
    issues = models.JSONField(default=list, blank=True)
    # Черновик отчёта от ИИ, когда сообщение не разобралось (проводит человек).
    draft = models.TextField(blank=True, default="")
    order = models.ForeignKey(
        "orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="bot_messages",
    )
    # Ответ в чат цитатой; отправляет бот, пока не получится.
    reply = models.TextField(blank=True, default="")
    reply_message_id = models.CharField(max_length=160, blank=True, default="")
    reply_sent_at = models.DateTimeField(null=True, blank=True)
    reply_attempts = models.PositiveSmallIntegerField(default=0)
    # Попытки обработки: сбой — ещё раз на следующем круге, до предела.
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=500, blank=True, default="")
    # Кто провёл или пропустил сообщение в журнале (у бота — пусто).
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_message_id"], name="bots_message_provider_id_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "-received_at"], name="bots_message_status_idx"),
            models.Index(fields=["chat_id", "provider_message_id"], name="bots_message_chat_idx"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.provider_message_id} ({self.get_status_display()})"


class WhatsAppBotSettings(models.Model):
    """Настройки WhatsApp-бота и его состояние — одна строка на приложение.

    Настройки правит администратор (``sys_permissions.manage``); поля
    состояния (номер, последний опрос, недавние чаты) пишет только процесс
    бота — отдельными ``update``, чтобы не перетирать друг друга.
    """

    singleton = models.BooleanField(default=True, unique=True, editable=False)
    enabled = models.BooleanField(default=False)
    # Чаты (группа «Отгрузка вагонов») и отправители, чьи отчёты бот разбирает.
    allowed_chat_ids = models.JSONField(default=list, blank=True)
    allowed_sender_ids = models.JSONField(default=list, blank=True)
    show_amounts_in_reply = models.BooleanField(default=False)
    # ±дней от даты отчёта (см. DEFAULT_DUPLICATE_WINDOW_DAYS).
    duplicate_window_days = models.PositiveSmallIntegerField(default=DEFAULT_DUPLICATE_WINDOW_DAYS)
    price_tolerance_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("15"))
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    # Состояние процесса бота (пишет только он).
    runtime_status = models.CharField(max_length=20, blank=True, default="")
    runtime_error = models.CharField(max_length=300, blank=True, default="")
    polled_at = models.DateTimeField(null=True, blank=True)
    instance_state = models.CharField(max_length=40, blank=True, default="")
    instance_state_at = models.DateTimeField(null=True, blank=True)
    # Недавние чаты, откуда писали боту: {chat_id: {"name", "at"}} — чтобы
    # выбрать группу в настройках, не зная её идентификатора.
    seen_chats = models.JSONField(default=dict, blank=True)

    CONFIG_FIELDS = (
        "enabled", "allowed_chat_ids", "allowed_sender_ids", "show_amounts_in_reply",
        "duplicate_window_days", "price_tolerance_pct", "updated_by", "updated_at",
    )

    @classmethod
    def load(cls) -> "WhatsAppBotSettings":
        settings_row, _ = cls.objects.get_or_create(singleton=True)
        return settings_row

    def __str__(self):
        return "Настройки WhatsApp-бота"
