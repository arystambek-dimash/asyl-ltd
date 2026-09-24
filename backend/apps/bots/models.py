from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.clients.models import Client
from apps.common.text import match_key

# Дубль вагона: тот же номер в живом заказе с датой отгрузки в пределах ±N
# дней от даты отчёта. Повторно присланный отчёт — той же датой, а раньше чем
# через 3 дня вагон под погрузку физически не вернётся (решение владельца).
DEFAULT_DUPLICATE_WINDOW_DAYS = 3
# Кому «Отправить отчёт» из истории грузчика (решение владельца, 24.09).
DEFAULT_REPORT_RECIPIENT = "Динара"
# Удачный круг процесса бота (runtime_status) и номер, который может
# отправлять сообщения (instance_state), — их пишет runner.
RUNTIME_RUNNING = "running"
INSTANCE_AUTHORIZED = "authorized"


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
    # «Отправить отчёт» в истории грузчика: кому (имя для кнопки «Отправить
    # Динаре») и номер WhatsApp — только цифры с кодом страны. Без номера бот
    # пишет в первую разрешённую группу, а ссылка открывает выбор чата.
    report_recipient_name = models.CharField(
        max_length=60, default=DEFAULT_REPORT_RECIPIENT, db_default=DEFAULT_REPORT_RECIPIENT)
    report_recipient_phone = models.CharField(max_length=15, blank=True, default="", db_default="")
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
        "duplicate_window_days", "price_tolerance_pct", "report_recipient_name", "report_recipient_phone",
        "updated_by", "updated_at",
    )

    @classmethod
    def load(cls) -> "WhatsAppBotSettings":
        settings_row, _ = cls.objects.get_or_create(singleton=True)
        return settings_row

    def is_alive(self, now=None) -> bool:
        """Процесс бота работает: последний круг удачный, номер готов, и круг не старше heartbeat.

        Веб-серверу не видны ни ключи Green-API, ни WHATSAPP_BOT_ENABLED
        контейнера бота — только его круги: без ключей бот пишет «degraded»,
        а остановленный или выключенный у себя перестаёт их отмечать.
        """
        max_age = timedelta(seconds=settings.WHATSAPP_BOT_HEARTBEAT_MAX_AGE_SECONDS)
        return (
            self.runtime_status == RUNTIME_RUNNING
            and self.instance_state == INSTANCE_AUTHORIZED
            and self.polled_at is not None
            and (now or timezone.now()) - self.polled_at <= max_age
        )

    def __str__(self):
        return "Настройки WhatsApp-бота"


class OutgoingMessage(models.Model):
    """«Отправить отчёт» о вагонах из истории грузчика — одна строка на нажатие.

    ``key`` — ключ нажатия с экрана: повтор того же запроса (двойное нажатие,
    сеть оборвалась до ответа) возвращает ту же строку, а не второе сообщение.
    Через бота — «В очереди»: процесс бота забирает её («Отправляется») и
    отправляет сам (:func:`apps.bots.wagon_report.send_pending_reports`).
    Отказ провайдера повторяется до предела и оставляет «Не отправлено» с
    ошибкой; бот не взял строку вовремя — тоже «Не отправлено». Ответа нет
    (сообщение могло уйти) или бот прервался посреди отправки — «Не
    подтверждено»: без повтора, человек проверяет WhatsApp. Ссылкой WhatsApp —
    сообщение отправил человек со своего телефона: бот такую строку не трогает.
    """

    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    UNKNOWN = "unknown"
    LINK = "link"
    STATUSES = [
        (QUEUED, "В очереди"),
        (SENDING, "Отправляется"),
        (SENT, "Отправлено"),
        (FAILED, "Не отправлено"),
        (UNKNOWN, "Не подтверждено"),
        (LINK, "Ссылкой WhatsApp"),
    ]

    key = models.CharField(max_length=64, unique=True)
    # Кому — как в настройках бота на момент отправки.
    recipient_name = models.CharField(max_length=60)
    phone = models.CharField(max_length=15, blank=True, default="")
    # Чат бота (номер@c.us или группа@g.us); у ссылки — пусто.
    chat_id = models.CharField(max_length=128, blank=True, default="")
    text = models.TextField()
    # Заказы отчёта — для журнала; отметка «отправлено» — в их отгрузках.
    order_ids = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES)
    provider_message_id = models.CharField(max_length=160, blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=500, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["status", "id"], name="bots_outgoing_status_idx")]

    def __str__(self):
        return f"Отчёт → {self.recipient_name} ({self.get_status_display()})"
