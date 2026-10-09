"""«Отправить отчёт» из истории грузчика: отгрузки вагонов → текст владельца → получателям в Telegram.

Текст (:func:`compose_rail_report`) — формат владельца
(:func:`apps.bots.parsing.format_rail_report`) для любой отгрузки вагонов:
отгруженной по отчёту (вагоны отгрузки) и кнопкой «Отгружено» (номер вагона
заказа и его позиции — «без номера», если номер не записан). Несколько
заказов — блоки по дню отгрузки, клиенту и станции через пустую строку, по
времени отгрузки. Коды товаров и названия клиентов читаются одним запросом
на всю страницу или период. Тот же текст бот присылает по команде /report
(:func:`period_report`).

Кому (:func:`report_recipients`) — список username из настроек Telegram-бота.
Бот может написать только тем, кто хоть раз написал ему (/start,
:class:`~apps.bots.models.BotChat`). «Отправить» ставит отчёт в очередь —
по строке доставки на каждого такого получателя
(:class:`~apps.bots.models.ReportDelivery`), и процесс бота отправляет их сам
(:func:`send_pending_reports`). Бот выключен или не работает — отправить
нельзя: экран так и говорит, а текст можно скопировать. Токен бота есть
только у процесса бота: веб-сервер сам в Telegram не пишет. Отгрузки
помечаются «отчёт отправлен», в журнал заказа пишется событие.

Бот не взял доставку за :data:`REPORT_QUEUE_TIMEOUT` — «Не отправлено», и
позже бот её уже не отправит: грузчик отправляет ещё раз. Отправка без ответа
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

from .models import BotChat, BotClientProfile, OutgoingMessage, ReportDelivery, TelegramBotSettings
from .parsing import KG_PER_TON, format_rail_report
from .providers.telegram import PRIVATE
from .rail import RAIL_TRANSPORT

# Почему «Отправить» недоступно: бот выключен или не работает, получатели
# не выбраны, никто из получателей ещё не написал боту /start.
BOT_OFF = "bot_off"
NO_RECIPIENTS = "no_recipients"
NOT_STARTED = "not_started"
BLOCKER_TEXTS = {
    BOT_OFF: "Telegram-бот сейчас не работает — отчёт не отправить. Скопируйте текст",
    NO_RECIPIENTS: "Не выбрано, кому отправлять отчёт: «Telegram-бот → Настройки»",
    NOT_STARTED: "Никто из получателей ещё не написал боту /start — боту некому отправить",
}
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


def product_codes(orders) -> dict[int, str]:
    """Товар → код отчёта: самое свежее написание из словаря («Д1с»). Его же пишет отчёт фур."""
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


def client_names(orders) -> dict[tuple[int, str], str]:
    """(клиент, валюта) → как клиента называют отчёты (последний профиль) — и вагонов, и фур."""
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
    # Платная и бонусная строки одного товара — один вагон: тонны складываются.
    tons: dict[str, Decimal] = {}
    for item in order.items.all():
        code = codes.get(item.product_id) or item.product_label
        tons[code] = tons.get(code, Decimal("0")) + item.quantity * Decimal(item.product_weight_kg or 0) / KG_PER_TON
    return [(code, number, weight) for code, weight in tons.items()]


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
    codes = product_codes(shipped)
    names = client_names(shipped)
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


# --- кому --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Recipient:
    username: str
    # Как человек подписан в Telegram (из его чата с ботом); пусто — не писал боту.
    name: str
    # Личный чат с ботом; пусто — бот ему написать не может.
    chat_id: str

    @property
    def ready(self) -> bool:
        return bool(self.chat_id)

    def payload(self) -> dict:
        return {"username": self.username, "name": self.name, "ready": self.ready}


def bot_sends(bot_settings: TelegramBotSettings) -> bool:
    """Бот отправляет сам: включён на сервере и в журнале, и его процесс работает."""
    return bool(settings.TELEGRAM_BOT_ENABLED and bot_settings.enabled and bot_settings.is_alive())


def private_chats(usernames) -> dict[str, BotChat]:
    """username → личный чат с ботом (последний) — одним запросом."""
    chats = BotChat.objects.filter(chat_type=PRIVATE, username__in=list(usernames)).order_by("last_message_at", "pk")
    return {chat.username: chat for chat in chats}


def report_recipients(bot_settings: TelegramBotSettings) -> list[Recipient]:
    """Получатели из настроек, по порядку, — и может ли бот каждому написать."""
    usernames = list(bot_settings.report_recipients or [])
    chats = private_chats(usernames)
    return [
        Recipient(username=username, name=chats[username].title if username in chats else "",
                  chat_id=chats[username].chat_id if username in chats else "")
        for username in usernames
    ]


def _blocker(bot_settings: TelegramBotSettings, recipients: list[Recipient]) -> str:
    if not bot_sends(bot_settings):
        return BOT_OFF
    if not recipients:
        return NO_RECIPIENTS
    if not any(recipient.ready for recipient in recipients):
        return NOT_STARTED
    return ""


def report_draft(orders) -> dict:
    """Ответ «Составить отчёт»: текст, заказы, кому уйдёт и можно ли отправить сейчас."""
    report = compose_rail_report(orders)
    bot_settings = TelegramBotSettings.load()
    recipients = report_recipients(bot_settings)
    blocker = _blocker(bot_settings, recipients)
    return {
        "text": report.text,
        "order_ids": report.order_ids,
        "recipients": [recipient.payload() for recipient in recipients],
        "can_send": not blocker,
        "reason": blocker,
    }


# --- отправка ------------------------------------------------------------------------------------


def recipient_label(delivery: ReportDelivery) -> str:
    """«@dinara_k»; у истории без username — имя, как его записали."""
    return f"@{delivery.username}" if delivery.username else delivery.name


_STATUS_LABELS = dict(ReportDelivery.STATUSES)


def delivery_state(delivery: ReportDelivery, now=None) -> tuple[str, str]:
    """Статус доставки для экрана и её ошибка (у «Не отправлено» и «Не подтверждено»).

    Бот не взял доставку за :data:`REPORT_QUEUE_TIMEOUT` — «Не отправлено»;
    прервался посреди отправки — «Не подтверждено». Экран не ждёт, пока бот
    сам переведёт такие строки: лежащий бот этого не сделает.
    """
    now = now or timezone.now()
    if delivery.status == ReportDelivery.QUEUED and delivery.created_at < now - REPORT_QUEUE_TIMEOUT:
        return ReportDelivery.FAILED, STALE_QUEUE_ERROR
    if delivery.status == ReportDelivery.SENDING and delivery.updated_at < now - SENDING_TIMEOUT:
        return ReportDelivery.UNKNOWN, INTERRUPTED_ERROR
    failed = delivery.status in (ReportDelivery.FAILED, ReportDelivery.UNKNOWN)
    return delivery.status, delivery.error if failed else ""


def delivery_payload(delivery: ReportDelivery, now=None) -> dict:
    status, error = delivery_state(delivery, now)
    return {"to": recipient_label(delivery), "status": status, "status_label": _STATUS_LABELS[status], "error": error}


def sent_payload(message: OutgoingMessage) -> dict:
    """Ответ «Отправить»: экран применяет его к строкам истории, а не перечитывает их."""
    now = timezone.now()
    return {
        "sent_at": message.created_at,
        "order_ids": list(message.order_ids),
        "deliveries": [delivery_payload(delivery, now) for delivery in message.deliveries.all()],
    }


@transaction.atomic
def send_wagon_report(orders, text: str, user, *, key: str) -> OutgoingMessage:
    """Поставить отчёт по отгрузкам ``orders`` в очередь бота — каждому получателю, кому бот может написать.

    ``key`` — ключ нажатия: повтор возвращает то же сообщение без второй
    отправки и второго события. Бот не работает или писать некому — отказ,
    экран показывает причину.
    """
    existing = OutgoingMessage.objects.filter(key=key).first()
    if existing is not None:
        return existing
    bot_settings = TelegramBotSettings.load()
    recipients = report_recipients(bot_settings)
    blocker = _blocker(bot_settings, recipients)
    if blocker:
        raise ValidationError({"detail": BLOCKER_TEXTS[blocker], "code": f"report_{blocker}"})
    message, created = OutgoingMessage.objects.get_or_create(key=key, defaults={
        "text": text, "order_ids": [order.pk for order in orders], "created_by": user,
    })
    if not created:
        return message
    ready = [recipient for recipient in recipients if recipient.ready]
    ReportDelivery.objects.bulk_create([
        ReportDelivery(message=message, username=recipient.username, name=recipient.name,
                       chat_id=recipient.chat_id, status=ReportDelivery.QUEUED)
        for recipient in ready
    ])
    Shipment.objects.filter(order_id__in=message.order_ids).update(
        report_sent_at=message.created_at, report_sent_by=user, report_message=message)
    to = ", ".join(f"@{recipient.username}" for recipient in ready)
    for order in orders:
        log_event(
            EVENT_TYPE,
            f"Отчёт о вагонах отправлен Telegram-ботом: {to}",
            user=user,
            order=order,
            payload={"message_id": message.pk, "recipients": [recipient.username for recipient in ready],
                     "orders": message.order_ids},
        )
    return message



def _expire_stale(now) -> None:
    """Перевести строки, которые экран уже показывает «Не отправлено» / «Не подтверждено» (:func:`delivery_state`)."""
    ReportDelivery.objects.filter(
        status=ReportDelivery.QUEUED, created_at__lt=now - REPORT_QUEUE_TIMEOUT,
    ).update(status=ReportDelivery.FAILED, error=STALE_QUEUE_ERROR, updated_at=now)
    ReportDelivery.objects.filter(
        status=ReportDelivery.SENDING, updated_at__lt=now - SENDING_TIMEOUT,
    ).update(status=ReportDelivery.UNKNOWN, error=INTERRUPTED_ERROR, updated_at=now)


def _send_parts(client: TelegramClient, delivery: ReportDelivery) -> str:
    """Отправить отчёт частями по 4096 знаков; вернуть ref первой части.

    Сбой после первой ушедшей части — :class:`TelegramOutcomeUnknown`:
    повтор задвоил бы уже полученное начало отчёта.
    """
    parts = split_text(delivery.message.text)
    first = ""
    for number, part in enumerate(parts, start=1):
        try:
            ref = client.send_message(delivery.chat_id, part)
        except TelegramError as exc:
            if number == 1:
                raise
            raise TelegramOutcomeUnknown(f"ушла часть отчёта ({number - 1} из {len(parts)}): {exc}") from exc
        first = first or ref
    return first


def send_pending_reports(client: TelegramClient, *, limit: int = 10) -> int:
    """Круг бота: отправить доставки из очереди. Сбой Telegram — попытка засчитана, ошибка
    наверх; отказ в чате получателя (4xx) — попытка засчитана, дальше следующая доставка.

    Перед отправкой строка забирается («Отправляется») — только если она ещё
    в очереди и не просрочена. Отправленное помечается сразу и больше не
    уходит. Отказ Telegram (сообщение точно не ушло) — снова в очередь, а
    после :data:`MAX_SEND_ATTEMPTS` отказов — «Не отправлено». Ответа нет
    (тайм-аут, обрыв, 5xx: сообщение могло уйти) или ушла только часть
    длинного отчёта — «Не подтверждено» без повтора, иначе получатель увидел
    бы отчёт дважды; так же — строка, на которой бот прервался. Оба случая
    видны в истории грузчика.
    """
    now = timezone.now()
    _expire_stale(now)
    fresh = now - REPORT_QUEUE_TIMEOUT
    sent = 0
    queue = ReportDelivery.objects.filter(status=ReportDelivery.QUEUED, created_at__gte=fresh)
    for delivery in queue.select_related("message").order_by("pk")[:limit]:
        claimed = ReportDelivery.objects.filter(
            pk=delivery.pk, status=ReportDelivery.QUEUED, created_at__gte=fresh,
        ).update(status=ReportDelivery.SENDING, updated_at=timezone.now())
        if not claimed:
            continue
        sending = ReportDelivery.objects.filter(pk=delivery.pk, status=ReportDelivery.SENDING)
        try:
            provider_id = _send_parts(client, delivery)
        except TelegramOutcomeUnknown as exc:
            sending.update(
                status=ReportDelivery.UNKNOWN, attempts=F("attempts") + 1, error=str(exc)[:500],
                updated_at=timezone.now(),
            )
            raise
        except TelegramError as exc:
            last = delivery.attempts + 1 >= MAX_SEND_ATTEMPTS
            sending.update(
                attempts=F("attempts") + 1, error=str(exc)[:500], updated_at=timezone.now(),
                status=ReportDelivery.FAILED if last else ReportDelivery.QUEUED,
            )
            # Отказ в этом чате (бот заблокирован, чата нет) — дело одной доставки.
            if isinstance(exc, TelegramRefused):
                continue
            raise
        sent_at = timezone.now()
        # Ушло — значит «Отправлено», даже если строку успели счесть прерванной.
        ReportDelivery.objects.filter(pk=delivery.pk).update(
            status=ReportDelivery.SENT, provider_message_id=provider_id[:160], sent_at=sent_at, error="",
            updated_at=sent_at,
        )
        sent += 1
    return sent
