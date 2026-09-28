"""«Отправить отчёт» из истории грузчика: отгрузки вагонов → текст владельца → Динаре.

Текст (:func:`compose_rail_report`) — формат владельца
(:func:`apps.bots.parsing.format_rail_report`) для любой отгрузки вагонов:
отгруженной по отчёту (вагоны отгрузки) и кнопкой «Отгружено» (номер вагона
заказа и его позиции — «без номера», если номер не записан). Несколько
заказов — блоки по дню отгрузки, клиенту и станции через пустую строку, по
времени отгрузки. Коды товаров и названия клиентов читаются одним запросом
на всю страницу или период. Тот же текст бот присылает по команде /report
(:func:`period_report`).

Кому (:func:`report_recipient`) — из настроек Telegram-бота («Динара» и её
username). Бот включён (флаг сервера TELEGRAM_BOT_ENABLED и выключатель в
журнале), его процесс работает (свежий удачный круг), а получатель хоть раз
написал боту (/start, :class:`~apps.bots.models.BotChat`) — сообщение встаёт
в очередь (:class:`~apps.bots.models.OutgoingMessage`), и процесс бота
отправляет его сам (:func:`send_pending_reports`). Иначе экран открывает чат
получателя в Telegram с готовым текстом — отправляет человек со своего
телефона. Токен бота есть только у процесса бота: веб-сервер сам в Telegram
не пишет. В обоих случаях отгрузки помечаются «отчёт отправлен», а в журнал
заказа пишется событие.

Бот не взял отчёт за :data:`REPORT_QUEUE_TIMEOUT` — «Не отправлено», и позже
бот его уже не отправит: грузчик отправляет ещё раз. Отправка без ответа
Telegram не повторяется сама (:func:`send_pending_reports`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.catalog.models import ProductAlias
from apps.common.telegram import (
    TelegramClient,
    TelegramError,
    TelegramOutcomeUnknown,
    TelegramRefused,
    split_text,
)
from apps.common.text import match_key
from apps.eventlog.services import log_event
from apps.orders.models import Order
from apps.orders.transport import order_wagons
from apps.shipments.models import Shipment

from .models import BotChat, BotClientProfile, OutgoingMessage, TelegramBotSettings
from .parsing import KG_PER_TON, format_rail_report
from .providers.telegram import PRIVATE
from .rail import RAIL_TRANSPORT

# Как отправить: ботом (очередь) или ссылкой Telegram с телефона человека.
BOT = "bot"
LINK = "link"
DELIVERIES = (BOT, LINK)
# Почему не ботом: бот выключен или не работает, username получателя не
# задан, получатель ещё не написал боту /start.
BOT_OFF = "bot_off"
NO_USERNAME = "no_username"
NOT_STARTED = "not_started"
# Строка заказа без номера вагона.
NO_NUMBER = "без номера"
# Отчёт за период — не больше стольких отгрузок: за неделю их десятки.
REPORT_MAX_ORDERS = 300
# Длинный отчёт бот отправляет несколькими сообщениями Telegram (до 4096 знаков).
REPORT_TEXT_MAX_LENGTH = 20000
# Отказ провайдера повторяется на следующих кругах бота, потом — «Не отправлено».
MAX_SEND_ATTEMPTS = 5
# Бот не взял отчёт из очереди за это время (процесс остановлен, нет токена
# Telegram) — «Не отправлено»: грузчик отправит ещё раз, бот его уже не шлёт.
REPORT_QUEUE_TIMEOUT = timedelta(minutes=10)
STALE_QUEUE_ERROR = f"бот не отправил за {int(REPORT_QUEUE_TIMEOUT.total_seconds()) // 60} минут"
# Отправка идёт не дольше тайм-аута запроса (15 с); дольше — бот прервался
# посреди неё, и дошло ли сообщение, неизвестно.
SENDING_TIMEOUT = timedelta(minutes=2)
INTERRUPTED_ERROR = "бот прервался во время отправки"
EVENT_TYPE = "rail_report"
_VOWELS = "аеёиоуыэюя"


# --- текст -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ComposedReport:
    text: str
    # Заказы отчёта по времени отгрузки.
    order_ids: list[int]


def _is_shipped_wagon_order(order) -> bool:
    shipment = getattr(order, "shipment", None)
    return (
        order.transport_type == RAIL_TRANSPORT
        and order.status == "shipped"
        and shipment is not None
        and shipment.shipped_at is not None
    )


def _product_codes(orders) -> dict[int, str]:
    """Товар → код отчёта: самое свежее написание из словаря («Д1с»)."""
    # Товары строк отчёта: вагоны отгрузки, а у отгрузки кнопкой — позиции заказа.
    product_ids = {
        goods.product_id
        for order in orders
        for goods in (order_wagons(order) or order.items.all())
        if goods.product_id
    }
    if not product_ids:
        return {}
    return {
        product_id: spelling or code
        for product_id, spelling, code in ProductAlias.objects.filter(product_id__in=product_ids)
        .order_by("created_at", "pk").values_list("product_id", "spelling", "code")
    }


def _client_names(orders) -> dict[tuple[int, str], str]:
    """(клиент, валюта) → как клиента называют отчёты (последний профиль)."""
    return {
        (client_id, currency): name
        for client_id, currency, name in BotClientProfile.objects.filter(
            client_id__in={order.client_id for order in orders},
        ).order_by("updated_at", "pk").values_list("client_id", "currency", "name")
    }


def _order_lines(order, codes: dict[int, str]) -> list[tuple[str, str, Decimal]]:
    """Строки вагонов заказа: (код товара, номер вагона, тонны).

    По отчёту — вагоны отгрузки. Кнопкой «Отгружено» — позиция заказа на
    строку: номер вагона заказа, тонны = мешки × фасовка.
    """
    wagons = order_wagons(order)
    if wagons:
        return [
            (codes.get(wagon.product_id) or wagon.product_label, wagon.number, wagon.weight_kg / KG_PER_TON)
            for wagon in wagons
        ]
    number = order.truck_number or NO_NUMBER
    return [
        (
            codes.get(item.product_id) or item.product_label,
            number,
            item.quantity * Decimal(item.product_weight_kg or 0) / KG_PER_TON,
        )
        for item in order.items.all()
    ]


def compose_rail_report(orders) -> ComposedReport:
    """Отчёт в формате владельца по отгруженным вагонным заказам (остальные пропускаются).

    ``orders`` — строки истории грузчика: клиент и отгрузка через
    ``select_related``, позиции и вагоны — предзагрузкой. Блок — день
    отгрузки, страна, клиент и станция; блоки и строки — по времени отгрузки.
    """
    shipped = sorted(
        (order for order in orders if _is_shipped_wagon_order(order)),
        key=lambda order: (order.shipment.shipped_at, order.pk),
    )
    if not shipped:
        return ComposedReport(text="", order_ids=[])
    codes = _product_codes(shipped)
    names = _client_names(shipped)
    blocks: dict[tuple, dict] = {}
    reported: list[int] = []
    for order in shipped:
        lines = _order_lines(order, codes)
        if not lines:
            continue
        reported.append(order.pk)
        day = timezone.localtime(order.shipment.shipped_at).date()
        client_name = names.get((order.client_id, order.currency)) or order.client.display_name
        station = " ".join(order.rail_station.split())
        key = (day, order.client.country, match_key(client_name), match_key(station))
        block = blocks.setdefault(key, {
            "day": day, "country": order.client.country, "client_name": client_name, "station": station,
            "wagons": [],
        })
        block["wagons"].extend(lines)
    return ComposedReport(
        text="\n\n".join(format_rail_report(**block) for block in blocks.values()),
        order_ids=reported,
    )


def period_report(date_from: date, date_to: date) -> ComposedReport:
    """Отчёт по всем отгрузкам вагонов за период (по дню выезда) — для /report бота."""
    orders = (
        Order.objects.filter(
            transport_type=RAIL_TRANSPORT,
            status="shipped",
            shipment__shipped_at__date__gte=date_from,
            shipment__shipped_at__date__lte=date_to,
        )
        .select_related("client__user", "shipment")
        .prefetch_related("items__product", "shipment__wagons__product")
        .order_by("shipment__shipped_at", "id")
    )
    return compose_rail_report(orders[:REPORT_MAX_ORDERS])


# --- кому и как ----------------------------------------------------------------------------------


def recipient_to(name: str) -> str:
    """Кому — в дательном падеже для «Отправить Динаре»: «Динара» → «Динаре».

    Только одно слово по окончанию; остальное (фамилия, латиница) — как есть.
    """
    if not name or " " in name or not name[-1].isalpha():
        return name
    last = name[-1].lower()

    def ending(text: str) -> str:
        return text.upper() if name[-1].isupper() else text

    if name.lower().endswith("ия"):  # Мария → Марии
        return name[:-1] + ending("и")
    if last in "ая":  # Динара → Динаре, Таня → Тане
        return name[:-1] + ending("е")
    if last == "й":  # Андрей → Андрею
        return name[:-1] + ending("ю")
    if "а" <= last <= "я" and last not in _VOWELS and last not in "ьъ":  # Азамат → Азамату
        return name + ending("у")
    return name


@dataclass(frozen=True)
class ReportRecipient:
    name: str
    to: str  # дательный падеж: «Динаре»
    username: str  # без «@» или пусто
    delivery: str  # BOT или LINK
    # Почему ссылкой, а не ботом (BOT_OFF, NO_USERNAME, NOT_STARTED); у бота — пусто.
    reason: str = ""
    chat_id: str = ""  # у бота: личный чат получателя

    def payload(self) -> dict:
        return {"name": self.name, "to": self.to, "username": self.username}


def bot_sends(bot_settings: TelegramBotSettings) -> bool:
    """Бот отправляет сам: включён на сервере и в журнале, и его процесс работает."""
    return bool(settings.TELEGRAM_BOT_ENABLED and bot_settings.enabled and bot_settings.is_alive())


def private_chat_id(username: str) -> str:
    """Личный чат человека с ботом по username; пусто — он ещё не писал боту."""
    if not username:
        return ""
    chat = BotChat.objects.filter(chat_type=PRIVATE, username=username).order_by("-last_message_at").first()
    return chat.chat_id if chat is not None else ""


def report_recipient(bot_settings: TelegramBotSettings | None = None) -> ReportRecipient:
    """Кому и как отправить отчёт сейчас.

    Бот включён и работает, а получатель писал боту — ботом в его личный
    чат. Иначе — ссылкой Telegram с телефона человека.
    """
    row = bot_settings or TelegramBotSettings.load()
    name, username = row.report_recipient_name, row.report_recipient_username
    chat_id = private_chat_id(username)
    if not bot_sends(row):
        reason = BOT_OFF
    elif not username:
        reason = NO_USERNAME
    elif not chat_id:
        reason = NOT_STARTED
    else:
        reason = ""
    return ReportRecipient(
        name=name, to=recipient_to(name), username=username, delivery=LINK if reason else BOT,
        reason=reason, chat_id="" if reason else chat_id,
    )


def report_draft(orders) -> dict:
    """Ответ «Составить отчёт»: текст, заказы, кому и как он уйдёт."""
    report = compose_rail_report(orders)
    recipient = report_recipient()
    return {
        "text": report.text,
        "order_ids": report.order_ids,
        "recipient": recipient.payload(),
        "delivery": recipient.delivery,
        "reason": recipient.reason,
    }


# --- отправка ------------------------------------------------------------------------------------


def _how(message: OutgoingMessage) -> str:
    return "ссылкой Telegram" if message.status == OutgoingMessage.LINK else "Telegram-ботом"


_STATUS_LABELS = dict(OutgoingMessage.STATUSES)


def message_state(message: OutgoingMessage, now=None) -> tuple[str, str]:
    """Статус отчёта для экрана и его ошибка (у «Не отправлено» и «Не подтверждено»).

    Бот не взял отчёт за :data:`REPORT_QUEUE_TIMEOUT` — «Не отправлено»;
    прервался посреди отправки — «Не подтверждено». Экран не ждёт, пока бот
    сам переведёт такие строки: лежащий бот этого не сделает.
    """
    now = now or timezone.now()
    if message.status == OutgoingMessage.QUEUED and message.created_at < now - REPORT_QUEUE_TIMEOUT:
        return OutgoingMessage.FAILED, STALE_QUEUE_ERROR
    if message.status == OutgoingMessage.SENDING and message.updated_at < now - SENDING_TIMEOUT:
        return OutgoingMessage.UNKNOWN, INTERRUPTED_ERROR
    failed = message.status in (OutgoingMessage.FAILED, OutgoingMessage.UNKNOWN)
    return message.status, message.error if failed else ""


def sent_payload(message: OutgoingMessage) -> dict:
    """Ответ «Отправить»: экран применяет его к строкам истории, а не перечитывает их."""
    status, error = message_state(message)
    payload = {
        "status": status,
        "status_label": _STATUS_LABELS[status],
        "sent_at": message.created_at,
        "order_ids": list(message.order_ids),
        "recipient": {"name": message.recipient_name, "to": recipient_to(message.recipient_name),
                      "username": message.username},
        "error": error,
    }
    return payload


@transaction.atomic
def send_wagon_report(orders, text: str, user, *, delivery: str, key: str) -> OutgoingMessage:
    """Отправить отчёт по отгрузкам ``orders``: в очередь бота или отметить отправку ссылкой.

    ``delivery`` — как его отправляет экран. Ссылка уже открыта на телефоне —
    её только записываем. Ботом — только пока бот может отправить: иначе
    отказ, и экран предложит ссылку. ``key`` — ключ нажатия: повтор возвращает то же
    сообщение без второй отправки и второго события.
    """
    existing = OutgoingMessage.objects.filter(key=key).first()
    if existing is not None:
        return existing
    recipient = report_recipient()
    if delivery == BOT and recipient.delivery != BOT:
        raise ValidationError({
            "detail": "Бот сейчас не может отправить отчёт — отправьте его через Telegram",
            "code": "report_bot_unavailable",
        })
    bot = delivery == BOT
    message, created = OutgoingMessage.objects.get_or_create(key=key, defaults={
        "recipient_name": recipient.name,
        "username": recipient.username,
        "chat_id": recipient.chat_id if bot else "",
        "text": text,
        "order_ids": [order.pk for order in orders],
        "status": OutgoingMessage.QUEUED if bot else OutgoingMessage.LINK,
        "created_by": user,
    })
    if not created:
        return message
    Shipment.objects.filter(order_id__in=message.order_ids).update(
        report_sent_at=message.created_at, report_sent_by=user, report_message=message)
    for order in orders:
        log_event(
            EVENT_TYPE,
            f"Отчёт о вагонах отправлен {recipient.to} {_how(message)}",
            user=user,
            order=order,
            payload={"message_id": message.pk, "delivery": delivery, "orders": message.order_ids},
        )
    return message


def _expire_stale(now) -> None:
    """Перевести строки, которые экран уже показывает «Не отправлено» / «Не подтверждено» (:func:`message_state`)."""
    OutgoingMessage.objects.filter(
        status=OutgoingMessage.QUEUED, created_at__lt=now - REPORT_QUEUE_TIMEOUT,
    ).update(status=OutgoingMessage.FAILED, error=STALE_QUEUE_ERROR, updated_at=now)
    OutgoingMessage.objects.filter(
        status=OutgoingMessage.SENDING, updated_at__lt=now - SENDING_TIMEOUT,
    ).update(status=OutgoingMessage.UNKNOWN, error=INTERRUPTED_ERROR, updated_at=now)


def _send_parts(client: TelegramClient, message: OutgoingMessage) -> str:
    """Отправить отчёт частями по 4096 знаков; вернуть ref первой части.

    Сбой после первой ушедшей части — :class:`TelegramOutcomeUnknown`:
    повтор задвоил бы уже полученное начало отчёта.
    """
    parts = split_text(message.text)
    first = ""
    for number, part in enumerate(parts, start=1):
        try:
            ref = client.send_message(message.chat_id, part)
        except TelegramError as exc:
            if number == 1:
                raise
            raise TelegramOutcomeUnknown(f"ушла часть отчёта ({number - 1} из {len(parts)}): {exc}") from exc
        first = first or ref
    return first


def send_pending_reports(client: TelegramClient, *, limit: int = 5) -> int:
    """Круг бота: отправить отчёты из очереди. Сбой Telegram — попытка засчитана, ошибка
    наверх; отказ в чате получателя (4xx) — попытка засчитана, дальше следующий отчёт.

    Перед отправкой строка забирается («Отправляется») — только если она ещё
    в очереди и не просрочена. Отправленное помечается сразу и больше не
    уходит. Отказ Telegram (сообщение точно не ушло) — снова в очередь, а
    после :data:`MAX_SEND_ATTEMPTS` отказов — «Не отправлено». Ответа нет
    (тайм-аут, обрыв, 5xx: сообщение могло уйти) или ушла только часть
    длинного отчёта — «Не подтверждено» без повтора, иначе Динара получила бы
    отчёт дважды; так же — строка, на которой бот прервался. Оба случая видны
    в истории грузчика.
    """
    now = timezone.now()
    _expire_stale(now)
    fresh = now - REPORT_QUEUE_TIMEOUT
    sent = 0
    queue = OutgoingMessage.objects.filter(status=OutgoingMessage.QUEUED, created_at__gte=fresh)
    for message in queue.order_by("pk")[:limit]:
        claimed = OutgoingMessage.objects.filter(
            pk=message.pk, status=OutgoingMessage.QUEUED, created_at__gte=fresh,
        ).update(status=OutgoingMessage.SENDING, updated_at=timezone.now())
        if not claimed:
            continue
        sending = OutgoingMessage.objects.filter(pk=message.pk, status=OutgoingMessage.SENDING)
        try:
            provider_id = _send_parts(client, message)
        except TelegramOutcomeUnknown as exc:
            sending.update(
                status=OutgoingMessage.UNKNOWN, attempts=F("attempts") + 1, error=str(exc)[:500],
                updated_at=timezone.now(),
            )
            raise
        except TelegramError as exc:
            last = message.attempts + 1 >= MAX_SEND_ATTEMPTS
            sending.update(
                attempts=F("attempts") + 1, error=str(exc)[:500], updated_at=timezone.now(),
                status=OutgoingMessage.FAILED if last else OutgoingMessage.QUEUED,
            )
            # Отказ в этом чате (бот заблокирован, чата нет) — дело одного отчёта.
            if isinstance(exc, TelegramRefused):
                continue
            raise
        sent_at = timezone.now()
        # Ушло — значит «Отправлено», даже если строку успели счесть прерванной.
        OutgoingMessage.objects.filter(pk=message.pk).update(
            status=OutgoingMessage.SENT, provider_message_id=provider_id[:160], sent_at=sent_at, error="",
            updated_at=sent_at,
        )
        sent += 1
    return sent
