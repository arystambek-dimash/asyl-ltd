from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.clients.models import Client
from apps.common.models import SingletonModel
from apps.common.money import CURRENCY_CHOICES
from apps.common.text import match_key

# Дубль вагона: тот же номер в живом заказе с датой отгрузки в пределах ±N
# дней от даты отчёта. Повторно присланный отчёт — той же датой, а раньше чем
# через 3 дня вагон под погрузку физически не вернётся (решение владельца).
DEFAULT_DUPLICATE_WINDOW_DAYS = 3
# Цена клиента дальше этого (в %) от цены прошлого вагонного заказа — на разбор.
DEFAULT_PRICE_TOLERANCE_PCT = Decimal("15")
# Удачный круг процесса бота (runtime_status) и рабочий токен бота
# (bot_state), — их пишет runner.
RUNTIME_RUNNING = "running"
BOT_AUTHORIZED = "authorized"
# Администратор бота по умолчанию (решение владельца, 28.09).
DEFAULT_ALLOWED_USERNAMES = ("d1maaash",)


def default_allowed_usernames() -> list[str]:
    return list(DEFAULT_ALLOWED_USERNAMES)


class BotClientProfile(models.Model):
    """Как отчёт о вагонах называет клиента и в какой валюте ему считать.

    «ООО OSIYO NAV NIHOL» в шапке отчёта → клиент CRM и валюта цены. Профиль
    нужен, когда название в отчёте не совпадает с карточкой клиента или валюта
    отгрузки отличается от валюты клиента по умолчанию. Пополняется при
    разборе отчёта (журнал бота и «Отгрузить по отчёту» у грузчика).
    """

    # Название как в отчёте — для людей; сравнение — по ключу.
    name = models.CharField(max_length=200)
    # apps.common.text.match_key(name): «ООО OSIYO» и «OOO "Osiyo"» — одно.
    name_key = models.CharField(max_length=200, unique=True)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="bot_profiles")
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES)
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
    обновления (Telegram отдаёт его, пока бот не подтвердит приём) ничего не
    проводит второй раз. ``status`` — что с сообщением сделали: провели сами,
    отправили человеку на разбор или пропустили. Строки с провайдером
    ``green_api`` — история прежнего WhatsApp-бота.
    """

    PROVIDER_TELEGRAM = "telegram"

    RECEIVED = "received"
    APPLIED = "applied"
    NEEDS_REVIEW = "needs_review"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    REJECTED = "rejected"
    IGNORED = "ignored"
    FAILED = "failed"
    STATUSES = [
        (RECEIVED, "Получено"),
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
    # Вкладки журнала → статусы (плюс «Все» без фильтра).
    TAB_STATUSES = {"review": REVIEW_STATUSES, "applied": (APPLIED,), "ignored": (IGNORED,)}

    MESSAGE = "message"
    EDITED = "edited"
    # Удаление присылал только WhatsApp; Telegram ботам удаления не сообщает.
    DELETED = "deleted"
    KINDS = [(MESSAGE, "Сообщение"), (EDITED, "Изменено"), (DELETED, "Удалено")]

    provider = models.CharField(max_length=20, default=PROVIDER_TELEGRAM)
    provider_message_id = models.CharField(max_length=160)
    chat_id = models.CharField(max_length=128)
    chat_name = models.CharField(max_length=200, blank=True, default="")
    sender_id = models.CharField(max_length=128, blank=True, default="")
    sender_name = models.CharField(max_length=200, blank=True, default="")
    # Username отправителя в Telegram (без «@», нижний регистр): по нему — доступ к боту.
    sender_username = models.CharField(max_length=64, blank=True, default="", db_default="")
    kind = models.CharField(max_length=10, choices=KINDS, default=MESSAGE)
    # Правка или удаление — исходное сообщение, если бот его видел.
    original = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="revisions",
    )
    # Не длиннее 8 КБ (``apps.bots.parsing.RAIL_REPORT_MAX_LENGTH``).
    text = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=24, choices=STATUSES, default=RECEIVED)
    # Итог разбора для журнала: клиент, станция, вагоны, тонны.
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


class TelegramBotSettings(SingletonModel):
    """Настройки Telegram-бота и его состояние — одна строка на приложение.

    Настройки правит администратор (``sys_permissions.manage``); поля
    состояния (username бота, последний опрос) пишет только процесс бота —
    отдельными ``update``, чтобы не перетирать друг друга.
    """

    enabled = models.BooleanField(default=False)
    # Кто пользуется ботом: username в Telegram без «@», нижним регистром.
    # Отчёты от остальных бот не проводит, а команды им не отвечают.
    allowed_usernames = models.JSONField(default=default_allowed_usernames, blank=True)
    show_amounts_in_reply = models.BooleanField(default=False)
    # ±дней от даты отчёта (см. DEFAULT_DUPLICATE_WINDOW_DAYS).
    duplicate_window_days = models.PositiveSmallIntegerField(default=DEFAULT_DUPLICATE_WINDOW_DAYS)
    # См. DEFAULT_PRICE_TOLERANCE_PCT.
    price_tolerance_pct = models.DecimalField(max_digits=5, decimal_places=2, default=DEFAULT_PRICE_TOLERANCE_PCT)
    # «Отправить отчёт» в истории грузчика: кому бот шлёт отчёт — username
    # без «@». Бот пишет только тем, кто хоть раз написал ему (/start).
    report_recipients = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    # Состояние процесса бота (пишет только он).
    runtime_status = models.CharField(max_length=20, blank=True, default="")
    runtime_error = models.CharField(max_length=300, blank=True, default="")
    polled_at = models.DateTimeField(null=True, blank=True)
    # getMe: authorized — токен рабочий, unauthorized — Telegram его отверг.
    bot_state = models.CharField(max_length=40, blank=True, default="")
    bot_state_at = models.DateTimeField(null=True, blank=True)
    bot_username = models.CharField(max_length=64, blank=True, default="")

    # Настройки, которые правит администратор (экран, журнал событий).
    SETTINGS_FIELDS = (
        "enabled", "allowed_usernames", "show_amounts_in_reply", "duplicate_window_days",
        "price_tolerance_pct", "report_recipients",
    )
    # Что сохраняет правка настроек: состояние бота пишет его процесс.
    CONFIG_FIELDS = (*SETTINGS_FIELDS, "updated_by", "updated_at")

    def is_alive(self, now=None) -> bool:
        """Процесс бота работает: последний круг удачный, токен рабочий, и круг не старше heartbeat.

        Веб-серверу не видны ни токен, ни TELEGRAM_BOT_ENABLED контейнера
        бота — только его круги: без токена бот пишет «degraded», а
        остановленный или выключенный у себя перестаёт их отмечать.
        """
        max_age = timedelta(seconds=settings.TELEGRAM_BOT_HEARTBEAT_MAX_AGE_SECONDS)
        return (
            self.runtime_status == RUNTIME_RUNNING
            and self.bot_state == BOT_AUTHORIZED
            and self.polled_at is not None
            and (now or timezone.now()) - self.polled_at <= max_age
        )

    def allows(self, username: str) -> bool:
        return bool(username) and username in (self.allowed_usernames or [])

    def __str__(self):
        return "Настройки Telegram-бота"


class BotChat(models.Model):
    """Чат, откуда писали боту: личный или группа — одна строка на чат.

    Telegram-бот может написать человеку только в чат, который тот открыл
    сам (/start), — по username отсюда находится чат получателя отчётов.
    Недавние чаты показываются в настройках: username не надо знать заранее.
    """

    chat_id = models.CharField(max_length=32, unique=True)
    # private, group, supergroup.
    chat_type = models.CharField(max_length=20)
    title = models.CharField(max_length=200, blank=True, default="")
    # У личного чата — username собеседника (без «@», нижний регистр).
    username = models.CharField(max_length=64, blank=True, default="")
    last_message_at = models.DateTimeField()

    class Meta:
        ordering = ["-last_message_at", "-pk"]
        indexes = [models.Index(fields=["username"], name="bots_chat_username_idx")]

    def __str__(self):
        return self.title or self.chat_id


class OutgoingMessage(models.Model):
    """«Отправить отчёт» о вагонах из истории грузчика — одна строка на нажатие.

    ``key`` — ключ нажатия с экрана: повтор того же запроса (двойное нажатие,
    сеть оборвалась до ответа) возвращает ту же строку, а не второе сообщение.
    Кому и дошло ли — в :class:`ReportDelivery`, по строке на получателя.
    """

    key = models.CharField(max_length=64, unique=True)
    text = models.TextField()
    # Заказы отчёта — для журнала; отметка «отправлено» — в их отгрузках.
    order_ids = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"Отчёт о вагонах №{self.pk}"


class ReportDelivery(models.Model):
    """Отчёт одному получателю: процесс бота забирает строку («Отправляется»)
    и отправляет сам (:func:`apps.bots.wagon_report.send_pending_reports`).

    Отказ Telegram повторяется до предела и оставляет «Не отправлено» с
    ошибкой; бот не взял строку вовремя — тоже «Не отправлено». Ответа нет
    (сообщение могло уйти) или бот прервался посреди отправки — «Не
    подтверждено»: без повтора, человек проверяет Telegram. «Ссылкой» —
    история: так отчёт отправлял человек со своего телефона до 29.09.
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
        (LINK, "Ссылкой"),
    ]

    message = models.ForeignKey(OutgoingMessage, on_delete=models.CASCADE, related_name="deliveries")
    # Кому: username без «@» и как человек подписан в Telegram на момент отправки.
    username = models.CharField(max_length=64, blank=True, default="")
    name = models.CharField(max_length=200, blank=True, default="")
    # Личный чат получателя с ботом.
    chat_id = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUSES)
    provider_message_id = models.CharField(max_length=160, blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["pk"]
        indexes = [models.Index(fields=["status", "id"], name="bots_delivery_status_idx")]

    def __str__(self):
        return f"Отчёт → @{self.username or self.name} ({self.get_status_display()})"
