"""WhatsApp-бот отчётов о вагонах: приём сообщений, проведение, ответы, журнал.

Процесс бота (``run_whatsapp_bot``) забирает уведомления у провайдера
(:mod:`apps.bots.providers.green_api`), записывает сообщение в базу
(:func:`ingest` — идемпотентно по идентификатору провайдера) и только потом
подтверждает приём. Дальше :func:`process_pending` разбирает отчёт и, если
всё сошлось, проводит его сам — той же операцией, что «Вставить отчёт» у
грузчика (:func:`apps.bots.rail.conduct_rail_report`), от имени сервисного
пользователя без права менять цены. Иначе сообщение ждёт человека в журнале
(«На проверке»). Правки и удаления отчётов бот не проводит — только на
разбор. Ответ в чат — цитатой исходного сообщения, отправляется отдельным
шагом (:func:`send_pending_replies`), пока не получится.

Сообщение бот обрабатывает под блокировкой его строки — как и «Провести» /
«Игнорировать» в журнале: решение человека бот не перетирает, а порядок
блокировок у обоих один (сообщение, потом клиент).
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal

from django.db import InterfaceError, OperationalError, transaction
from django.db.models import Count, F, Q
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from apps.clients.models import Client
from apps.common.money import MONEY_PLACES
from apps.common.text import group_digits, plural_ru
from apps.eventlog.services import log_event
from apps.orders.models import Order
from apps.orders.transport import order_wagons

from . import llm
from .models import BotMessage, WhatsAppBotSettings
from .parsing import (
    KG_PER_TON,
    RAIL_REPORT_MAX_LENGTH,
    RailReport,
    ReportIssue,
    decimal_string,
    format_tons,
    parse_rail_report,
)
from .providers.green_api import GreenApiClient, GreenApiError, IncomingMessage
from .rail import PRICE_MISMATCH, apply_rail_report, resolve_report

log = logging.getLogger(__name__)

# Сбой обработки сообщения повторяется на следующих кругах, потом — к человеку.
MAX_ATTEMPTS = 3
MAX_REPLY_ATTEMPTS = 5
# В ответе «на проверке» — первые причины, остальные числом.
REPLY_ISSUES = 3
REPLY_MAX_LENGTH = 1000
SEEN_CHATS_LIMIT = 20
EVENT_TYPE = "whatsapp_bot"
# Причины, при которых отчёт не разобрался по формату, — повод для черновика ИИ.
STRUCTURE_ISSUES = frozenset({
    "header_missing", "station_missing", "no_wagons", "unknown_line", "bad_date",
    "client_missing", "wagon_count_missing",
})
# Номер вагона в свободном тексте: сообщение похоже на отчёт, даже если не по формату.
_WAGON_NUMBER = re.compile(r"(?<!\d)\d{8}(?!\d)")
# Отказ проведения (права, склад, заказ): в журнале — текст отказа, в чат — нет.
NOT_APPLIED = "not_applied"
# Последняя попытка обработки не удалась — сообщение ждёт человека.
FAILED_REPLY = "Принято, на проверке: не удалось провести автоматически"


# --- настройки и приём --------------------------------------------------------------------------


def _chat_allowed(bot_settings: WhatsAppBotSettings, chat_id: str) -> bool:
    return chat_id in (bot_settings.allowed_chat_ids or [])


def _sender_allowed(bot_settings: WhatsAppBotSettings, sender_id: str) -> bool:
    return sender_id in (bot_settings.allowed_sender_ids or [])


def _is_group(chat_id: str) -> bool:
    return chat_id.endswith("@g.us")


def remember_seen_chat(bot_settings: WhatsAppBotSettings, incoming: IncomingMessage) -> None:
    """Недавние чаты для настроек: идентификатор группы не надо знать заранее."""
    seen = dict(bot_settings.seen_chats or {})
    seen[incoming.chat_id] = {"name": incoming.chat_name, "at": timezone.now().isoformat()}
    recent = sorted(seen.items(), key=lambda item: item[1].get("at") or "", reverse=True)[:SEEN_CHATS_LIMIT]
    bot_settings.seen_chats = dict(recent)
    WhatsAppBotSettings.objects.filter(pk=bot_settings.pk).update(seen_chats=bot_settings.seen_chats)


def _provider_id(incoming: IncomingMessage) -> str:
    # Правка и удаление — отдельные записи: не совпадают с исходным сообщением
    # и между собой (каждая правка — своё время), но повтор доставки — тот же.
    if incoming.kind == BotMessage.MESSAGE:
        return incoming.message_id
    stamp = int(incoming.sent_at.timestamp()) if incoming.sent_at else 0
    return f"{incoming.kind}:{incoming.message_id}:{stamp}"


def ingest(incoming: IncomingMessage, bot_settings: WhatsAppBotSettings) -> BotMessage | None:
    """Записать сообщение; повтор доставки — та же запись.

    Разрешённый чат — к разбору. Группа, которой нет в настройках, — сразу
    «Пропущено»: группа попадает в «недавние» только с первым сообщением, и
    отчёты, пришедшие до того, как её выбрали, не теряются — их проводят из
    журнала. Личные сообщения чужих чатов не хранятся: остаётся только чат в
    «недавних».
    """
    remember_seen_chat(bot_settings, incoming)
    allowed = _chat_allowed(bot_settings, incoming.chat_id)
    if not allowed and not _is_group(incoming.chat_id):
        return None
    skipped = {} if allowed else {
        "status": BotMessage.IGNORED,
        "issues": [_note(
            "chat_not_allowed", "Чат не в списке разрешённых — выберите его в настройках бота",
            subject=incoming.chat_id)],
    }
    original = None
    if incoming.target_id:
        original = BotMessage.objects.filter(
            provider=BotMessage.PROVIDER_GREEN_API,
            chat_id=incoming.chat_id,
            provider_message_id=incoming.target_id,
            kind=BotMessage.MESSAGE,
        ).first()
    message, _ = BotMessage.objects.get_or_create(
        provider=BotMessage.PROVIDER_GREEN_API,
        provider_message_id=_provider_id(incoming),
        defaults={
            "chat_id": incoming.chat_id,
            "chat_name": incoming.chat_name[:200],
            "sender_id": incoming.sender_id[:128],
            "sender_name": incoming.sender_name[:200],
            "kind": incoming.kind,
            "original": original,
            # Длиннее отчёта не бывает: храним начало, сообщение — «Отклонено».
            "text": incoming.text[: RAIL_REPORT_MAX_LENGTH + 1],
            "sent_at": incoming.sent_at,
            **skipped,
        },
    )
    return message


# --- тексты -----------------------------------------------------------------------------------------


def _note(code: str, message: str, *, subject: str = "", order_id: int | None = None) -> dict:
    """Причина от бота (не из разбора отчёта) — в том же виде, что причины разбора."""
    return ReportIssue(code, message, subject=subject, order_id=order_id).as_dict()


def _money(value) -> str:
    return group_digits(Decimal(value).quantize(MONEY_PLACES))


def applied_reply(order: Order, *, show_amounts: bool) -> str:
    """«Проведено: заказ №N, КЛИЕНТ, ст. X, 12 вагонов, 816 т (16 320 мешков Мука …)»."""
    wagons = order_wagons(order)
    items = list(order.items.select_related("product"))
    bags = sum(item.quantity for item in items)
    tons = sum((wagon.weight_kg for wagon in wagons), Decimal("0")) / KG_PER_TON
    if len(items) == 1:
        goods = f" {items[0].product_label}"
    else:
        goods = ": " + ", ".join(f"{item.product_label} — {group_digits(item.quantity)}" for item in items)
    parts = [f"заказ №{order.pk}", order.client.display_name]
    if order.rail_station:
        parts.append(f"ст. {order.rail_station}")
    parts.append(f"{len(wagons)} {plural_ru(len(wagons), 'вагон', 'вагона', 'вагонов')}")
    parts.append(
        f"{format_tons(tons)} т ({group_digits(bags)} {plural_ru(bags, 'мешок', 'мешка', 'мешков')}{goods})")
    text = "Проведено: " + ", ".join(parts)
    if show_amounts:
        text += f"; сумма {_money(order.total_amount)} {order.currency}"
    return text[:REPLY_MAX_LENGTH]


def _chat_reason(issue: dict, *, show_amounts: bool) -> str:
    """Причина для чата. Полный текст — в журнале; в чат не уходят цены (только
    если суммы в ответе включены) и внутренние отказы проведения."""
    if issue.get("code") == PRICE_MISMATCH and not show_amounts:
        subject = f"«{issue['subject']}»: " if issue.get("subject") else ""
        return f"{subject}цена отличается от прошлого вагонного заказа"
    if issue.get("code") == NOT_APPLIED:
        return "нужна проверка человеком"
    return issue["message"]


def review_reply(issues: list[dict], *, show_amounts: bool = False) -> str:
    """«Принято, на проверке: <причины>» — первые причины и сколько ещё."""
    messages = [_chat_reason(issue, show_amounts=show_amounts) for issue in issues[:REPLY_ISSUES]]
    rest = len(issues) - len(messages)
    text = "Принято, на проверке: " + "; ".join(messages or ["нужна проверка человеком"])
    if rest > 0:
        text += f" и ещё {rest} {plural_ru(rest, 'причина', 'причины', 'причин')}"
    return text if len(text) <= REPLY_MAX_LENGTH else text[: REPLY_MAX_LENGTH - 1] + "…"


def report_summary(report: RailReport, client: Client | None = None) -> dict:
    """Итог разбора для строки журнала — без денег: их показывает предпросмотр по правам.

    ``client`` — распознанный клиент: журнал показывает его вместо названия из отчёта.
    """
    return {
        "client_name": report.client_name,
        "client": {"name": client.display_name} if client is not None else None,
        "station": report.station,
        "wagons": len(report.wagons),
        "tons": decimal_string(report.total_tons),
    }


def is_report_candidate(report: RailReport, text: str) -> bool:
    """Похоже ли сообщение на отчёт: «ок», «спасибо» в чате — не повод для разбора."""
    codes = {issue.code for issue in report.issues}
    structured = bool(report.wagons) or "header_missing" not in codes or "station_missing" not in codes
    return structured or bool(_WAGON_NUMBER.search(text))


# --- обработка --------------------------------------------------------------------------------------


def _finish(message: BotMessage, status: str, *, issues=(), reply="", order=None, draft=None, error="") -> None:
    """Итог обработки. ``draft=None`` — черновик ИИ остаётся (решение человека его не стирает)."""
    message.status = status
    message.issues = list(issues)
    message.order = order if order is not None else message.order
    if draft is not None:
        message.draft = draft
    message.error = error[:500]
    if reply:
        message.reply = reply
        message.reply_sent_at = None
        message.reply_attempts = 0
    message.save()


def _error_text(detail) -> str:
    if isinstance(detail, dict):
        detail = detail.get("detail", next(iter(detail.values()), ""))
    if isinstance(detail, list):
        return "; ".join(_error_text(item) for item in detail)
    return str(detail)


def _error_issue(exc: APIException) -> dict:
    return _note(NOT_APPLIED, f"Не проведено: {_error_text(exc.detail)}")


def _mark_applied(message: BotMessage, order: Order, bot_settings: WhatsAppBotSettings) -> None:
    """Проведено: «Проведено: …» цитатой исходного сообщения."""
    # Цитата — исходное сообщение; удалось ли её отправить — дело бота.
    reply = applied_reply(order, show_amounts=bot_settings.show_amounts_in_reply) if _quote_id(message) else ""
    _finish(message, BotMessage.APPLIED, order=order, reply=reply)


def _revises_applied(message: BotMessage) -> bool:
    original = message.original
    return message.kind != BotMessage.MESSAGE and original is not None and original.status == BotMessage.APPLIED


def _review_revision(message: BotMessage, *, quote: bool) -> None:
    """Правка или удаление отчёта — всегда к человеку (бот их не проводит).

    ``quote`` — ответить в чат (только разрешённому отправителю).
    """
    original = message.original
    applied = f" — по нему уже проведён заказ №{original.order_id}, проверьте заказ" if (
        original is not None and original.order_id) else ""
    if message.kind == BotMessage.DELETED:
        if original is None or original.status == BotMessage.IGNORED:
            _finish(message, BotMessage.IGNORED)
            return
        _finish(message, BotMessage.NEEDS_REVIEW, order=original.order, issues=[
            _note("message_deleted", f"Сообщение удалено отправителем{applied}", order_id=original.order_id)])
        return
    report = parse_rail_report(message.text)
    if not is_report_candidate(report, message.text) and (original is None or original.status == BotMessage.IGNORED):
        _finish(message, BotMessage.IGNORED)
        return
    message.parsed = report_summary(report)
    issue = _note(
        "message_edited", f"Сообщение изменено после отправки{applied}",
        order_id=original.order_id if original is not None else None)
    # Цитировать можно только сообщение, которое бот видел.
    reply = review_reply([issue]) if original is not None and quote else ""
    _finish(message, BotMessage.NEEDS_REVIEW, order=original.order if original else None,
            issues=[issue], reply=reply)


# Ждут бота: новые и упавшие, пока не кончились попытки.
_PENDING = Q(status=BotMessage.RECEIVED) | Q(
    status=BotMessage.FAILED, attempts__lt=MAX_ATTEMPTS)


def pending_messages():
    return BotMessage.objects.filter(_PENDING).order_by("received_at", "pk")


def _lock_pending(message: BotMessage) -> BotMessage | None:
    """Свежая строка под блокировкой, если сообщение всё ещё ждёт бота.

    Пока бот до него добирался, человек мог провести или пропустить его в
    журнале — тогда ``None``.
    """
    return BotMessage.objects.select_for_update().filter(_PENDING, pk=message.pk).first()


def _process(message: BotMessage, *, user, bot_settings: WhatsAppBotSettings) -> None:
    """Разбор и проведение сообщения, заблокированного :func:`_lock_pending`."""
    sender_allowed = _sender_allowed(bot_settings, message.sender_id)
    # Бот слушает только разрешённых отправителей. Исключение — правка или
    # удаление проведённого отчёта (например, админ группы удалил отчёт):
    # это видит человек, но в чат бот не отвечает.
    if not sender_allowed and not _revises_applied(message):
        _finish(message, BotMessage.IGNORED, issues=[
            _note("sender_not_allowed", "Отправитель не в списке разрешённых", subject=message.sender_id)])
        return
    if message.kind != BotMessage.MESSAGE:
        _review_revision(message, quote=sender_allowed)
        return
    if len(message.text) > RAIL_REPORT_MAX_LENGTH:
        _finish(message, BotMessage.REJECTED, issues=[
            _note("too_long", "Сообщение слишком длинное — пришлите один отчёт")])
        return
    report = parse_rail_report(message.text)
    if not is_report_candidate(report, message.text):
        _finish(message, BotMessage.IGNORED)
        return
    resolved = resolve_report(report, user=user)
    message.parsed = report_summary(report, resolved.client)
    if resolved.ok:
        try:
            # Точка сохранения: отказ откатывает только проведение.
            with transaction.atomic():
                order = apply_rail_report(report, user)
        except (ValidationError, PermissionDenied) as exc:
            # Отчёт мог провести человек у грузчика («Вставить отчёт»):
            # свежие причины важнее текста отказа.
            resolved = resolve_report(report, user=user)
            issues = [issue.as_dict() for issue in resolved.issues] or [_error_issue(exc)]
        else:
            _mark_applied(message, order, bot_settings)
            return
    else:
        issues = [issue.as_dict() for issue in resolved.issues]
    _finish(message, BotMessage.NEEDS_REVIEW, issues=issues,
            reply=review_reply(issues, show_amounts=bot_settings.show_amounts_in_reply))


def _fail(message: BotMessage, exc: Exception) -> BotMessage:
    """Сбой обработки: повтор на следующих кругах, после последней попытки — к человеку."""
    with transaction.atomic():
        locked = _lock_pending(message)
        if locked is None:
            return message
        locked.attempts += 1
        last = locked.attempts >= MAX_ATTEMPTS
        _finish(locked, BotMessage.FAILED, issues=locked.issues, error=f"{type(exc).__name__}: {exc}",
                reply=FAILED_REPLY if last else "")
    return locked


def _attach_draft(message: BotMessage) -> BotMessage:
    """Черновик ИИ, если сообщение на проверке не разобралось по формату.

    Без блокировки: ответа ИИ ждут до 45 с, а «Провести» человека ждать его
    не должно. Черновик ложится, только если сообщение всё ещё на проверке.
    Бот по черновику не проводит — только человек в журнале.
    """
    if message.status != BotMessage.NEEDS_REVIEW or message.kind != BotMessage.MESSAGE:
        return message
    report = parse_rail_report(message.text)
    if not ({issue.code for issue in report.issues} & STRUCTURE_ISSUES):
        return message
    sent_on = timezone.localtime(message.sent_at).date() if message.sent_at else timezone.localdate()
    draft = llm.draft_report(message.text, sent_on=sent_on)
    if not draft:
        return message
    with transaction.atomic():
        locked = BotMessage.objects.select_for_update().filter(
            pk=message.pk, status=BotMessage.NEEDS_REVIEW, draft="").first()
        if locked is None:
            return message
        # В чат — причины отчёта (ответ уже есть); про черновик знает только журнал.
        _finish(locked, BotMessage.AWAITING_CONFIRMATION, draft=draft, issues=[*locked.issues, _note(
            "llm_draft", "Сообщение не по формату — ИИ подготовил черновик, проверьте его")])
    return locked


def process_message(message: BotMessage, *, user, bot_settings: WhatsAppBotSettings) -> BotMessage:
    """Разобрать и, если всё сошлось, провести отчёт от имени сервисного ``user``.

    Сообщение, которое уже решил человек, бот пропускает (см. :func:`_lock_pending`).
    """
    try:
        with transaction.atomic():
            locked = _lock_pending(message)
            if locked is None:
                return message
            _process(locked, user=user, bot_settings=bot_settings)
    except (OperationalError, InterfaceError):
        raise
    except Exception as exc:  # сбой одного сообщения не останавливает бота
        log.exception("WhatsApp bot message %s failed", message.pk)
        return _fail(message, exc)
    return _attach_draft(locked)


def process_pending(*, user, bot_settings: WhatsAppBotSettings, limit: int = 20) -> int:
    messages = list(pending_messages()[:limit])
    for message in messages:
        process_message(message, user=user, bot_settings=bot_settings)
    return len(messages)


def _quote_id(message: BotMessage) -> str:
    if message.kind == BotMessage.MESSAGE:
        return message.provider_message_id
    return message.original.provider_message_id if message.original_id else ""


def send_pending_replies(client: GreenApiClient, *, limit: int = 10) -> int:
    """Отправить ответы цитатой. Сбой провайдера — попытка засчитана, ошибка наверх.

    Текст сбоя в ``error`` сообщения не пишется: там причина сбоя обработки
    (:func:`_fail`), а сбой Green-API видно в состоянии бота и в «не отправлен
    (попыток: N)» у ответа.

    Отправленным отмечается только тот ответ, что ушёл: если, пока он уходил,
    человек провёл сообщение («Проведено» после «на проверке»), новый ответ
    уйдёт следующим кругом.
    """
    sent = 0
    messages = BotMessage.objects.filter(
        reply_sent_at__isnull=True, reply_attempts__lt=MAX_REPLY_ATTEMPTS,
    ).exclude(reply="").select_related("original").order_by("pk")[:limit]
    for message in messages:
        same_reply = BotMessage.objects.filter(pk=message.pk, reply=message.reply, reply_sent_at__isnull=True)
        try:
            reply_id = client.send_message(message.chat_id, message.reply, quoted_message_id=_quote_id(message))
        except GreenApiError:
            same_reply.update(reply_attempts=F("reply_attempts") + 1, updated_at=timezone.now())
            raise
        same_reply.update(reply_message_id=reply_id[:160], reply_sent_at=timezone.now(), updated_at=timezone.now())
        sent += 1
    return sent


# --- журнал: действия человека ------------------------------------------------------------------------


def _lock_open(message: BotMessage) -> BotMessage:
    message = BotMessage.objects.select_for_update().get(pk=message.pk)
    if message.status == BotMessage.APPLIED:
        raise ValidationError({
            "detail": f"Сообщение уже проведено: заказ №{message.order_id}", "code": "bot_message_applied",
        })
    return message


@transaction.atomic
def apply_message(message: BotMessage, user, *, text: str | None = None, order: Order | None = None) -> BotMessage:
    """«Провести» из журнала от имени человека — с его правами и отделом.

    ``text`` — исправленный текст (или черновик ИИ); без него — как пришло.
    ``order`` — «Отгрузить по отчёту» похожий ручной заказ. В чат уходит
    «Проведено: …» цитатой исходного сообщения.
    """
    message = _lock_open(message)
    if message.kind == BotMessage.DELETED:
        raise ValidationError({"detail": "Удалённое сообщение не проводится", "code": "bot_message_deleted"})
    source = message.text if text is None else text
    report = parse_rail_report(source)
    bot_settings = WhatsAppBotSettings.load()
    applied = apply_rail_report(report, user, order=order)
    message.parsed = report_summary(report, applied.client)
    message.resolved_by, message.resolved_at = user, timezone.now()
    _mark_applied(message, applied, bot_settings)
    log_event(
        EVENT_TYPE,
        f"Сообщение WhatsApp проведено вручную: заказ №{applied.pk}",
        user=user,
        order=applied,
        payload={"message_id": message.pk, **({"text": source} if source != message.text else {})},
    )
    return message


@transaction.atomic
def ignore_message(message: BotMessage, user) -> BotMessage:
    """«Игнорировать»: сообщение не отчёт или уже внесено иначе. Склад и заказы не трогаются."""
    message = _lock_open(message)
    message.resolved_by, message.resolved_at = user, timezone.now()
    _finish(message, BotMessage.IGNORED, issues=message.issues)
    return message


def status_counts() -> dict[str, int]:
    """Сколько сообщений в каждой вкладке журнала — одним запросом."""
    counts = BotMessage.objects.aggregate(
        **{tab: Count("pk", filter=Q(status__in=statuses)) for tab, statuses in BotMessage.TAB_STATUSES.items()},
        all=Count("pk"),
    )
    return {key: value or 0 for key, value in counts.items()}
