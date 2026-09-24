"""«Отправить отчёт» из истории грузчика: отгрузки вагонов → текст владельца → Динаре.

Текст (:func:`compose_rail_report`) — формат владельца
(:func:`apps.bots.parsing.format_rail_report`) для любой отгрузки вагонов:
отгруженной по отчёту (вагоны отгрузки) и кнопкой «Отгружено» (номер вагона
заказа и его позиции — «без номера», если номер не записан). Несколько
заказов — блоки по дню отгрузки, клиенту и станции через пустую строку, по
времени отгрузки. Коды товаров и названия клиентов читаются одним запросом
на всю страницу или период.

Кому (:func:`report_recipient`) — из настроек WhatsApp-бота («Динара» и её
номер). Бот включён (флаг сервера WHATSAPP_BOT_ENABLED и выключатель в
журнале) и его процесс работает (свежий удачный круг) — сообщение встаёт в
очередь (:class:`~apps.bots.models.OutgoingMessage`), и процесс бота
отправляет его сам (:func:`send_pending_reports`); без номера получателя бот
пишет в первую разрешённую группу. Иначе экран открывает ссылку wa.me с
текстом — отправляет человек со своего телефона (без номера — сам выбирает
чат). Ключи Green-API есть только у процесса бота: веб-сервер сам в WhatsApp
не пишет. В обоих случаях отгрузки помечаются «отчёт отправлен», а в журнал
заказа пишется событие.

Бот не взял отчёт за :data:`REPORT_QUEUE_TIMEOUT` — «Не отправлено», и позже
бот его уже не отправит: грузчик отправляет ещё раз. Отправка без ответа
провайдера не повторяется сама (:func:`send_pending_reports`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.catalog.models import ProductAlias
from apps.common.text import match_key
from apps.eventlog.services import log_event
from apps.orders.transport import order_wagons
from apps.shipments.models import Shipment

from .models import BotClientProfile, OutgoingMessage, WhatsAppBotSettings
from .parsing import KG_PER_TON, format_rail_report
from .providers.green_api import GreenApiClient, GreenApiError, GreenApiOutcomeUnknown
from .rail import RAIL_TRANSPORT

# Как отправить: ботом (очередь) или ссылкой WhatsApp с телефона человека.
BOT = "bot"
LINK = "link"
DELIVERIES = (BOT, LINK)
# Строка заказа без номера вагона.
NO_NUMBER = "без номера"
# Отчёт за период — не больше стольких отгрузок: за неделю их десятки.
REPORT_MAX_ORDERS = 300
# Предел текста Green-API sendMessage.
REPORT_TEXT_MAX_LENGTH = 20000
# Отказ провайдера повторяется на следующих кругах бота, потом — «Не отправлено».
MAX_SEND_ATTEMPTS = 5
# Бот не взял отчёт из очереди за это время (процесс остановлен, нет ключей
# Green-API) — «Не отправлено»: грузчик отправит ещё раз, бот его уже не шлёт.
REPORT_QUEUE_TIMEOUT = timedelta(minutes=10)
STALE_QUEUE_ERROR = f"бот не отправил за {int(REPORT_QUEUE_TIMEOUT.total_seconds()) // 60} минут"
# Отправка идёт не дольше тайм-аута запроса (15 с); дольше — бот прервался
# посреди неё, и дошло ли сообщение, неизвестно.
SENDING_TIMEOUT = timedelta(minutes=2)
INTERRUPTED_ERROR = "бот прервался во время отправки"
EVENT_TYPE = "rail_report"
_WHATSAPP_LINK = "https://wa.me/"
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
    phone: str  # только цифры с кодом страны или пусто
    delivery: str  # BOT или LINK
    chat_id: str = ""  # у бота: номер@c.us или группа@g.us
    chat_name: str = ""  # у бота без номера: название группы

    def payload(self) -> dict:
        return {"name": self.name, "to": self.to, "phone": self.phone, "chat_name": self.chat_name}


def bot_sends(bot_settings: WhatsAppBotSettings) -> bool:
    """Бот отправляет сам: включён на сервере и в журнале, и его процесс работает."""
    return bool(settings.WHATSAPP_BOT_ENABLED and bot_settings.enabled and bot_settings.is_alive())


def report_recipient(bot_settings: WhatsAppBotSettings | None = None) -> ReportRecipient:
    """Кому и как отправить отчёт сейчас.

    Бот включён и работает — ботом: на номер получателя, а без номера — в
    первую разрешённую группу (иначе в первый разрешённый чат). Некуда, бот
    выключен или не отмечает круги — ссылкой WhatsApp.
    """
    row = bot_settings or WhatsAppBotSettings.load()
    name = row.report_recipient_name
    phone = row.report_recipient_phone
    chat_id = chat_name = ""
    if bot_sends(row):
        if phone:
            chat_id = f"{phone}@c.us"
        else:
            chats = list(row.allowed_chat_ids or [])
            chat_id = next((chat for chat in chats if chat.endswith("@g.us")), chats[0] if chats else "")
            chat_name = ((row.seen_chats or {}).get(chat_id) or {}).get("name", "") if chat_id else ""
    return ReportRecipient(
        name=name, to=recipient_to(name), phone=phone, delivery=BOT if chat_id else LINK,
        chat_id=chat_id, chat_name=chat_name,
    )


def whatsapp_link(phone: str, text: str) -> str:
    """Ссылка wa.me с готовым текстом: на номер или, без номера, с выбором чата."""
    return f"{_WHATSAPP_LINK}{phone}?text={quote(text, safe='')}"


def report_draft(orders) -> dict:
    """Ответ «Составить отчёт»: текст, заказы, кому и как он уйдёт."""
    report = compose_rail_report(orders)
    recipient = report_recipient()
    draft = {
        "text": report.text,
        "order_ids": report.order_ids,
        "recipient": recipient.payload(),
        "delivery": recipient.delivery,
    }
    if recipient.delivery == LINK:
        draft["link"] = whatsapp_link(recipient.phone, report.text)
    return draft


# --- отправка ------------------------------------------------------------------------------------


def _how(message: OutgoingMessage) -> str:
    return "через WhatsApp" if message.status == OutgoingMessage.LINK else "ботом WhatsApp"


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
                      "phone": message.phone},
        "error": error,
    }
    if message.status == OutgoingMessage.LINK:
        payload["link"] = whatsapp_link(message.phone, message.text)
    return payload


@transaction.atomic
def send_wagon_report(orders, text: str, user, *, delivery: str, key: str) -> OutgoingMessage:
    """Отправить отчёт по отгрузкам ``orders``: в очередь бота или отметить отправку ссылкой.

    ``delivery`` — как его отправляет экран. Ссылка уже открыта на телефоне —
    её только записываем. Ботом — только пока бот включён: иначе отказ, и
    экран предложит ссылку. ``key`` — ключ нажатия: повтор возвращает то же
    сообщение без второй отправки и второго события.
    """
    existing = OutgoingMessage.objects.filter(key=key).first()
    if existing is not None:
        return existing
    recipient = report_recipient()
    if delivery == BOT and recipient.delivery != BOT:
        raise ValidationError({
            "detail": "Бот сейчас не отправляет сообщения — отправьте отчёт через WhatsApp",
            "code": "report_bot_unavailable",
        })
    bot = delivery == BOT
    message, created = OutgoingMessage.objects.get_or_create(key=key, defaults={
        "recipient_name": recipient.name,
        "phone": recipient.phone,
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


def send_pending_reports(client: GreenApiClient, *, limit: int = 5) -> int:
    """Круг бота: отправить отчёты из очереди. Сбой провайдера — попытка засчитана, ошибка наверх.

    Перед отправкой строка забирается («Отправляется») — только если она ещё
    в очереди и не просрочена. Отправленное помечается сразу и больше не
    уходит. Отказ провайдера (сообщение точно не ушло) — снова в очередь, а
    после :data:`MAX_SEND_ATTEMPTS` отказов — «Не отправлено». Ответа нет
    (тайм-аут, обрыв, 5xx: сообщение могло уйти) — «Не подтверждено» без
    повтора, иначе Динара получила бы отчёт дважды; так же — строка, на
    которой бот прервался. Оба случая видны в истории грузчика.
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
            provider_id = client.send_message(message.chat_id, message.text)
        except GreenApiOutcomeUnknown as exc:
            sending.update(
                status=OutgoingMessage.UNKNOWN, attempts=F("attempts") + 1, error=str(exc)[:500],
                updated_at=timezone.now(),
            )
            raise
        except GreenApiError as exc:
            last = message.attempts + 1 >= MAX_SEND_ATTEMPTS
            sending.update(
                attempts=F("attempts") + 1, error=str(exc)[:500], updated_at=timezone.now(),
                status=OutgoingMessage.FAILED if last else OutgoingMessage.QUEUED,
            )
            raise
        sent_at = timezone.now()
        # Ушло — значит «Отправлено», даже если строку успели счесть прерванной.
        OutgoingMessage.objects.filter(pk=message.pk).update(
            status=OutgoingMessage.SENT, provider_message_id=provider_id[:160], sent_at=sent_at, error="",
            updated_at=sent_at,
        )
        sent += 1
    return sent
