from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product
from apps.eventlog.services import log_event
from apps.notifications.services import notify
from apps.orders.backdate import backdate_events, backdate_moment
from apps.orders.statuses import AWAITING_SHIPMENT_STATUSES, CAMERA_BINDING_STATUSES, ON_POST_STATUSES
from apps.warehouse.services import deduct_stock, lock_stock_items

from .access import assert_can_ship
from .models import Shipment, ShipmentWagon

LOADING_CAMERA_CONSTRAINT = "orders_one_active_order_per_loading_camera"
# Куда возвращают незавершённую или отменённую отгрузку.
ROLLBACK_TARGET_STATUSES = ("pending", "confirmed", "cancelled")


def _is_loading_camera_conflict(exc: IntegrityError) -> bool:
    cause = exc.__cause__
    diagnostic = getattr(cause, "diag", None)
    return getattr(diagnostic, "constraint_name", None) == LOADING_CAMERA_CONSTRAINT


def _camera_busy_error(exc: IntegrityError) -> ValidationError:
    return ValidationError({
        "detail": "Камера уже закреплена за другим активным заказом",
        "code": "camera_busy",
    })


def _locked(order, user=None):
    """Перечитать заказ под блокировкой строки. Переходы статуса — это
    read-check-write: без блокировки двойной клик или две вкладки провели бы
    один шаг дважды (у отгрузки — двойное списание склада и двойной долг)."""
    from apps.orders.services import lock_live_order

    return lock_live_order(order, user)


def _validate_loading_camera_available(order, camera: str) -> None:
    if not camera:
        return
    conflict = (
        # Do not lock the conflicting Order here: two different camera starts
        # may each already own their own parent/session and attempt a swap,
        # which would create an O_A -> O_B / O_B -> O_A deadlock. AI starts
        # are session-serialized under the shared camera mutex, and the named
        # partial UNIQUE constraint remains the final arbiter.
        type(order).objects
        .filter(
            loading_camera=camera,
            status__in=CAMERA_BINDING_STATUSES,
            deleted_at__isnull=True,
        )
        .exclude(pk=order.pk)
        .only("id")
        .first()
    )
    if conflict:
        raise ValidationError({
            "detail": f"Камера уже закреплена за заказом #{conflict.pk}",
            "code": "camera_busy",
            "order_id": conflict.pk,
        })


def can_start_loading(order, camera: str) -> bool:
    """Загрузку начинают у подтверждённого или прибывшего заказа; идущую
    погрузку на той же камере можно восстановить повторным стартом."""
    restoring_same_binding = order.status == "loading" and order.loading_camera == camera
    return order.status in ("confirmed", "arrived") or restoring_same_binding


def _require_shipment(order):
    shipment = getattr(order, "shipment", None)
    if shipment is None:
        raise ValidationError(
            {"detail": "Для заказа нет записи загрузки",
             "code": "shipment_required"}
        )
    return shipment


def _require_transport(order, kind):
    if order.transport_type != kind:
        raise ValidationError(
            {"detail": "Этот шаг недоступен для выбранного вида транспорта",
             "code": "wrong_transport"}
        )


def estimated_load_kg(order) -> Decimal:
    """Расчётный вес груза по мешкам: Σ(кол-во × вес фасовки)."""
    return sum(
        (i.quantity * i.product_weight_kg for i in order.items.all()), Decimal(0)
    )


def _lock_order_stock(order, *, refusal: str):
    """Позиции заказа и его склад с остатками под блокировкой — для отгрузки и отката.

    Склад выбран, пока заказ ещё правился: его могут потом выключить, но
    отгрузка и её откат идут по этому закреплённому складу. Удалённый товар
    не списать и не вернуть — ``refusal`` начинает текст отказа.
    """
    items = list(order.items.select_related("product").order_by("product_id", "id"))
    deleted = [item.product_label for item in items if item.product_id is None]
    if deleted:
        raise ValidationError({
            "detail": f"{refusal}: удалены товары — " + ", ".join(deleted),
            "code": "product_deleted",
        })
    warehouse = order.warehouse
    lock_stock_items((item.product for item in items), warehouse)
    return items, warehouse


@transaction.atomic
def begin_camera_loading(
    order,
    camera: str,
    user,
):
    """Закрепить свободную камеру и перевести заказ в активную погрузку.

    Моноблок вызывает эту операцию после подтверждения AI-сессии. Заказ из
    `confirmed` покидает очередь готовых к погрузке в момент фактического старта
    с выбранной камерой. Одна камера может принадлежать только одному живому
    заказу; ограничение продублировано частичным UNIQUE-индексом в PostgreSQL.
    """
    order = _locked(order, user)
    if not can_start_loading(order, camera):
        raise ValidationError({
            "detail": "Загрузку можно начать только для подтверждённого или прибывшего заказа",
            "code": "invalid_status",
        })

    _validate_loading_camera_available(order, camera)

    now = timezone.now()
    old_status = order.status
    shipment, _ = Shipment.objects.select_for_update().get_or_create(order=order)
    shipment.loading_started_at = shipment.loading_started_at or now
    shipment.save(update_fields=["loading_started_at"])

    order.status = "loading"
    order.loading_camera = camera
    try:
        # Isolate the constraint failure in a savepoint so it can be mapped to
        # ValidationError. counting.start then runs its normal compensation
        # path instead of leaking a 500 and an orphan STARTING reservation.
        with transaction.atomic():
            order.save(update_fields=["status", "loading_camera"])
    except IntegrityError as exc:
        if not _is_loading_camera_conflict(exc):
            raise
        raise _camera_busy_error(exc) from exc
    if old_status != "loading":
        log_event(
            "loading_start",
            "Начата загрузка через Моноблок",
            user=user,
            order=order,
            payload={"camera": camera, "from": old_status},
        )
    log_event(
        "camera_bound",
        f"Камера {camera} закреплена за заказом",
        user=user,
        order=order,
        payload={"camera": camera},
    )
    return order


def has_open_ai_session(order) -> bool:
    """Идёт ли по заказу AI-подсчёт: открытая сессия держит заказ и камеру."""
    # Local import avoids a shipments -> cameras -> shipments import cycle.
    from apps.cameras.models import AiCountingSession

    return AiCountingSession.objects.filter(
        order_id=order.pk,
        status__in=AiCountingSession.OPEN_STATUSES,
    ).exists()


def assert_no_open_ai_session(order) -> None:
    """Открытый AI-подсчёт закрывает только отгрузка: заказ до неё не трогаем."""
    if has_open_ai_session(order):
        raise ValidationError({
            "detail": "По заказу идёт AI-подсчёт — отгрузите его на странице «Грузчик»",
            "code": "ai_session_active",
        })


def _valid_ai_total(bags) -> bool:
    """Годное число мешков от воркера: целое неотрицательное, но не bool."""
    return not isinstance(bags, bool) and isinstance(bags, int) and bags >= 0


@transaction.atomic
def finish_ai_counting(order, bags: int, user):
    """Сохранить финальный AI-счёт и завершить загрузку.

    Воркер на ПК цеха — сторонний процесс, и его ответ может прийти пустым
    или битым. Раньше это роняло завершение посреди разбора AI-сессии: заказ
    оставался в ``loading`` с открытой сессией, а её наличие блокировало и
    ручное завершение, и откат.
    Поэтому негодное число не блокирует завершение подсчёта: за факт берётся
    заказанное количество, а расхождение попадает в журнал.
    """
    order = _locked(order, user)

    if order.status != "loading":
        raise ValidationError({
            "detail": "Завершить можно только идущую загрузку",
            "code": "invalid_status",
        })
    shipment = _require_shipment(order)

    source = "ai_final"
    if not _valid_ai_total(bags):
        rejected, bags = bags, order.ordered_bags
        source = "ai_final_fallback"
        log_event(
            "loading",
            f"AI-сервис вернул некорректный счёт — принято по заказу: {bags} мешков",
            user=user,
            order=order,
            payload={"bags": bags, "source": source,
                     "rejected_total": repr(rejected)},
        )

    shipment.bags_loaded = bags
    shipment.save(update_fields=["bags_loaded"])
    audit = {"bags": bags, "source": source}
    log_event(
        "loading",
        f"AI-подсчёт зафиксирован: {bags} мешков",
        user=user,
        order=order,
        payload=audit,
    )
    log_event(
        "loading_done",
        "Загрузка завершена по финальному AI-подсчёту",
        user=user,
        order=order,
        payload=audit,
    )
    order.status = "loaded"
    order.loading_camera = ""
    order.save(update_fields=["status", "loading_camera"])
    return shipment


@transaction.atomic
def manual_complete_order(order, bags: int | None, user):
    """Завершить подтверждённый заказ без привязки к камере.

    Это административный путь для борда и списка заказов. В отличие от голой
    смены ``status`` он создаёт полноценную Shipment, фиксирует количество,
    списывает склад и освобождает возможную старую привязку камеры. Отсутствие
    ``bags`` означает «без ручного подсчёта»: используем количество из заказа.
    Работающую AI-сессию отсюда не обрываем: её закрывает «Отгружено» у грузчика.
    """
    order = _locked(order, user)
    if order.status not in AWAITING_SHIPMENT_STATUSES:
        raise ValidationError({
            "detail": "Вручную завершить можно только подтверждённый или загружаемый заказ",
            "code": "invalid_status",
        })

    assert_no_open_ai_session(order)

    existing_shipment = Shipment.objects.filter(order=order).first()
    if bags is None:
        if existing_shipment is not None and order.status in ON_POST_STATUSES:
            bags = existing_shipment.bags_loaded
            count_source = "current"
        else:
            bags = order.ordered_bags
            count_source = "ordered"
    else:
        if not _valid_ai_total(bags):
            raise ValidationError({
                "detail": "Количество мешков должно быть целым числом от 0",
                "code": "invalid_bags",
            })
        count_source = "manual"

    now = timezone.now()
    shipment = existing_shipment or Shipment.objects.create(order=order)
    if order.transport_type == "truck":
        if shipment.weigh_in_kg is None:
            shipment.weigh_in_kg = estimated_load_kg(order)
        shipment.arrived_at = shipment.arrived_at or now
    shipment.loading_started_at = shipment.loading_started_at or now
    shipment.bags_loaded = bags
    shipment.save()
    log_event(
        "loading_done",
        f"Отгрузка завершена вручную: {bags} мешков",
        user=user,
        order=order,
        payload={"bags": bags, "source": "manual_override", "count_source": count_source},
    )
    label = (
        "Вагон: отгрузка завершена вручную"
        if order.transport_type == "train"
        else f"Машина {order.truck_number}: отгрузка завершена вручную"
    )
    return _do_ship(order, shipment, user, label)


@transaction.atomic
def rewind_loading(order, user, target_status="confirmed"):
    """Вернуть въехавший/загружаемый заказ обратно в ожидание въезда.

    Это отдельная бизнес-операция, а не голая ручная смена статуса: очищаем
    незавершённую отгрузку и освобождаем назначенную камеру. Работающую
    AI-сессию откат не обрывает: её закрывает «Отгружено» у грузчика.
    """
    order = _locked(order, user)

    if target_status not in ROLLBACK_TARGET_STATUSES:
        raise ValidationError({
            "detail": "Недопустимый целевой статус возврата",
            "code": "bad_status",
        })
    if order.status not in ON_POST_STATUSES:
        raise ValidationError({
            "detail": "Вернуть можно только незавершённую отгрузку",
            "code": "invalid_status",
        })

    assert_no_open_ai_session(order)
    from apps.orders.services import assert_money_allows_status

    # Возврат в ожидание сохраняет деньги предоплатой; в заявку или отмену — без денег.
    assert_money_allows_status(order, target_status)

    old = order.status
    shipment = getattr(order, "shipment", None)
    reset_bags = shipment.bags_loaded if shipment else 0
    if shipment:
        shipment.delete()
    order.status = target_status
    order.loading_camera = ""
    order.save(update_fields=["status", "loading_camera"])
    target_labels = {
        "pending": "на рассмотрение",
        "confirmed": "в ожидание въезда",
        "cancelled": "в отменённые",
    }
    log_event(
        "shipping_rewind",
        f"Незавершённая отгрузка сброшена; заказ переведён {target_labels[target_status]}",
        user=user,
        order=order,
        payload={"from": old, "to": target_status, "reset_bags": reset_bags},
    )
    return order


# Сколько времени грузчик может сам отменить свою отгрузку: ошибку замечают
# сразу, а спустя час заказ уже живёт в кассе и складе — там нужен откат по праву.
LOADER_ROLLBACK_WINDOW = timedelta(hours=1)


def loader_rollback_blocker(order, user) -> str:
    """Почему грузчик не может отменить эту отгрузку; пустая строка — можно."""
    from apps.eventlog.models import EventLog

    if order.status != "shipped":
        return "Отменить можно только отгруженный заказ"
    shipment = getattr(order, "shipment", None)
    if shipment is None or shipment.shipped_at is None:
        return "У заказа нет отгрузки"
    # Окно — от момента отгрузки, а не записи: отгрузку задним числом (отчёт
    # о вагонах за прошедший день, фиксация) грузчик сам не отменяет. Иначе
    # списку очереди пришлось бы читать журнал по каждой строке.
    if timezone.now() - shipment.shipped_at > LOADER_ROLLBACK_WINDOW:
        return "Прошло больше часа — отмену оформляет старший в «Заказах»"
    # Оплата не мешает: заказ возвращается в ожидание, деньги остаются предоплатой.
    last = (
        EventLog.objects.filter(event_type="shipment", order=order)
        .order_by("-created_at", "-id")
        .values_list("user_id", flat=True)
        .first()
    )
    if last != getattr(user, "pk", None):
        return "Отменить может только тот, кто отгрузил"
    return ""


@transaction.atomic
def rollback_shipment(order, user, *, target_status: str, reason: str):
    """Controlled reversal of a completed shipment.

    The operation is deliberately separate from generic status editing: it
    restores stock, clears shipment state and writes an immutable audit entry
    with the author and required reason. Camera recordings are left to the
    camera PC's MediaMTX retention (``recordings.VIDEO_RETENTION_DAYS``).
    """
    order = _locked(order, user)
    if target_status not in ROLLBACK_TARGET_STATUSES:
        raise ValidationError({
            "detail": "Вернуть отгруженный заказ можно на рассмотрение, в ожидание или в отменённые",
            "code": "bad_status",
        })
    reason = " ".join(str(reason or "").split())
    if len(reason) < 5:
        raise ValidationError({
            "detail": "Укажите причину отката (минимум 5 символов)",
            "code": "rollback_reason_required",
        })
    if len(reason) > 500:
        raise ValidationError({"detail": "Причина слишком длинная", "code": "reason_too_long"})
    if order.status != "shipped":
        raise ValidationError({
            "detail": "Откат доступен только для отгруженного заказа",
            "code": "invalid_status",
        })
    from apps.orders.debt import order_payment_status
    from apps.orders.services import assert_money_allows_status

    # Возврат в ожидание сохраняет деньги предоплатой; в заявку или отмену — без денег.
    assert_money_allows_status(order, target_status)

    items, warehouse = _lock_order_stock(order, refusal="Нельзя восстановить склад")

    # Видео отгрузки не удаляем: у ПК камер нет API удаления записей, а сетевые
    # вызовы внутри складской транзакции держали бы блокировку. Запись исчезнет
    # по сроку хранения MediaMTX на ПК камер.
    from apps.cameras.models import AiCountingSession
    session_ids = list(
        AiCountingSession.objects.filter(order=order).values_list("pk", flat=True)
    )
    shipment = Shipment.objects.select_for_update().filter(order=order).first()

    from apps.warehouse.services import adjust_stock

    restored = 0
    for item in items:
        adjust_stock(
            item.product,
            item.quantity,
            user,
            note=f"Откат отгрузки заказа #{order.pk}: {reason}",
            warehouse=warehouse,
            require_active=False,
        )
        restored += item.quantity

    previous_bags = shipment.bags_loaded if shipment else 0
    if shipment:
        shipment.delete()
    AiCountingSession.objects.filter(pk__in=session_ids).exclude(
        recording_stream="",
    ).update(
        error="Отгрузка отменена; видео будет удалено по сроку хранения ПК камер",
    )
    order.status = target_status
    order.loading_camera = ""
    # Деньги остаются предоплатой: статус оплаты — по факту, а не «не оплачен».
    order.payment_status = order_payment_status(order)
    order.save(update_fields=["status", "payment_status", "loading_camera"])
    log_event(
        "shipment_rollback",
        f"Отгрузка заказа #{order.pk} отменена. Причина: {reason}",
        user=user,
        order=order,
        payload={
            "from": "shipped", "to": target_status, "reason": reason,
            "restored_bags": restored, "previous_bags_loaded": previous_bags,
            "recording_session_ids": session_ids,
        },
    )
    return order


def mark_order_shipped(order, user):
    """Перевести заказ в «Отгружено» и записать долг по неоплаченному остатку.

    Возвращает событие «долг» (или None), чтобы отгрузку задним числом можно
    было перенести на её день вместе с ним.
    """
    from apps.orders.debt import order_payment_status, order_remaining

    order.status = "shipped"
    order.loading_camera = ""
    # Предоплата переживает отгрузку: статус оплаты — по факту денег.
    order.payment_status = order_payment_status(order)
    order.save(update_fields=["status", "payment_status", "loading_camera"])
    # Неоплаченный остаток отгруженного заказа — долг клиента (orders/debt.py),
    # как бы он ни собирался платить. Предоплаченная часть долгом не становится.
    remaining = order_remaining(order)
    if remaining <= 0:
        return None
    return log_event(
        "debt",
        f"Заказ отгружен в долг: {remaining}",
        user=user,
        order=order,
        payload={
            "amount": str(remaining),
            "intent": order.settlement_intent,
        },
    )


def _do_ship(order, shipment, user, label, *, shipped_at: datetime | None = None):
    """Списать со склада и зафиксировать отгрузку. Общее для трака и вагона.

    ``shipped_at`` — отгрузка по отчёту за прошедший день: момент выезда и
    события отгрузки и долга переносятся на этот день (сводки читают их по
    дате события), склад списывается как обычно.
    """
    from apps.orders.transport import client_transport_phrase

    items, warehouse = _lock_order_stock(order, refusal="Нельзя отгрузить")
    for item in items:
        deduct_stock(
            item.product,
            item.quantity,
            user,
            warehouse=warehouse,
            require_active=False,
        )
    shipment.shipped_at = shipped_at or timezone.now()
    shipment.save()
    debt_event = mark_order_shipped(order, user)
    bag_estimate = estimated_load_kg(order)
    shipment_event = log_event(
        "shipment", label, user=user, order=order,
        payload={"bags_loaded": shipment.bags_loaded,
                 "bag_estimate_kg": str(bag_estimate),
                 "amount": str(order.total_amount),
                 "settlement_intent": order.settlement_intent,
                 "weigh_in_kg": (
                     str(shipment.weigh_in_kg)
                     if shipment.weigh_in_kg is not None else None
                 )})
    if shipped_at is not None:
        backdate_events([shipment_event, debt_event], shipped_at)
    # Единственное уведомление клиенту об отгрузке. Машина на территории —
    # номер клиенту не называем; вагоны отчёта — «12 ваг., ст. …», без номеров.
    transport = client_transport_phrase(order, joiner=" / ")
    notify(order.client, f"Заказ №{order.pk} отгружен" + (f" ({transport})" if transport else ""))
    return shipment


@transaction.atomic
def dispatch_order(order, user, *, truck_number: str = "", trailer_number: str | None = None):
    """Грузчик: одна кнопка — заказ отгружен на заказанное количество.

    Без въезда, счёта мешков и камер: списание со склада, долг и журнал — общие
    с отгрузкой по отчёту и ручным завершением (``_do_ship``). Номер накладной — номер заказа.
    Пустой номер тягача — «не менять». Прицеп: ``None`` — «не менять», пустая
    строка — «стереть», как в форме заказа и «Фурах» (экран грузчика шлёт
    только исправленные номера, устаревший экран чужой прицеп не сотрёт).
    """
    from apps.orders.transport import set_order_transport

    order = _locked(order, user)
    assert_can_ship(user, order)
    if order.status not in AWAITING_SHIPMENT_STATUSES:
        raise ValidationError({
            "detail": "Отгрузить можно только подтверждённый заказ, который ещё не выехал",
            "code": "invalid_status",
        })
    assert_no_open_ai_session(order)
    truck_number = (truck_number or "").strip() or None
    trailer_number = None if trailer_number is None else trailer_number.strip()
    if truck_number is not None or trailer_number is not None:
        # Клиенту об отгрузке сообщит _do_ship — одним уведомлением.
        set_order_transport(order, user, truck=truck_number, trailer=trailer_number, notify_client=False)
    shipment, _ = Shipment.objects.get_or_create(order=order)
    if not shipment.bags_loaded:
        shipment.bags_loaded = order.ordered_bags
    label = (
        f"Вагон отгружен по накладной №{order.pk}"
        if order.transport_type == "train"
        else f"Отгружено по накладной №{order.pk}"
    )
    return _do_ship(order, shipment, user, label)


def loader_dispatch(order, user, *, truck_number: str = "", trailer_number: str | None = None):
    """Кнопка «Отгружено» грузчика: право и номер — до закрытия AI-подсчёта.

    Отказ по области или опечатка в номере не должны останавливать сессию
    камер: сначала проверки, затем открытый подсчёт закрывает сама отгрузка
    (кнопок погрузки в Моноблоке нет), затем ``dispatch_order``. Ошибки ПК
    камер (``ai.AiUnavailable``/``ai.AiError``) пробрасываются: заказ остаётся
    как был, грузчик повторит.
    """
    # Local imports avoid a shipments -> cameras/orders -> shipments import cycle.
    from apps.cameras import counting
    from apps.orders.transport import check_transport_change

    assert_can_ship(user, order)
    check_transport_change(
        order, user, truck=(truck_number or "").strip() or None, trailer=trailer_number, ignore_ai_session=True)
    counting.close_session_for_dispatch(order, user)
    return dispatch_order(order, user, truck_number=truck_number, trailer_number=trailer_number)


@dataclass(frozen=True)
class RailWagon:
    """Вагон из отчёта об отгрузке: номер, товар, мешки и вес в кг."""

    number: str
    product: Product
    bags: int
    weight_kg: Decimal


def rail_bags_mismatch(order, wagons) -> str:
    """«Д1с: в заказе 4080, в отчёте 2720» по каждому расхождению; пусто — сошлось."""
    ordered: Counter = Counter()
    labels = {}
    for item in order.items.all():
        ordered[item.product_id] += item.quantity
        labels[item.product_id] = item.product_label
    reported: Counter = Counter()
    for wagon in wagons:
        reported[wagon.product.pk] += wagon.bags
        labels.setdefault(wagon.product.pk, str(wagon.product))
    return "; ".join(
        f"«{labels[product_id]}»: в заказе {ordered[product_id]}, в отчёте {reported[product_id]}"
        for product_id in sorted(ordered.keys() | reported.keys(), key=lambda pk: labels[pk])
        if ordered[product_id] != reported[product_id]
    )


@transaction.atomic
def ship_rail_report(order, wagons, user, *, station: str, shipped_day: date):
    """Отгрузить вагонный заказ по отчёту: вагоны, склад и долг — одной транзакцией.

    Мешки отчёта по каждому товару должны совпасть с заказом: заранее
    внесённый заказ с другим количеством сначала правят (склад и долг не
    должны разойтись с вагонами). Отчёт за прошедший день датирует отгрузку
    полднем того дня (:func:`_do_ship`); такую отгрузку отменяет только старший
    в «Заказах» — часовое окно грузчика (:func:`loader_rollback_blocker`)
    считается от даты отгрузки. Сессию камер (``ShippingLoadingSession``)
    отчёт не трогает — вагоны под аркой считаются отдельно.
    """
    from apps.orders.transport import rail_phrase

    order = _locked(order, user)
    assert_can_ship(user, order)
    _require_transport(order, "train")
    if order.status not in AWAITING_SHIPMENT_STATUSES:
        raise ValidationError({
            "detail": "По отчёту отгружается только подтверждённый вагонный заказ, который ещё не отгружен",
            "code": "invalid_status",
        })
    wagons = list(wagons)
    if not wagons:
        raise ValidationError({"detail": "В отчёте нет вагонов", "code": "rail_no_wagons"})
    repeated = sorted(number for number, count in Counter(w.number for w in wagons).items() if count > 1)
    if repeated:
        raise ValidationError({
            "detail": f"Вагон указан дважды: {', '.join(repeated)}",
            "code": "rail_duplicate_wagon",
        })
    today = timezone.localdate()
    if shipped_day > today:
        raise ValidationError({"detail": "Дата отчёта ещё не наступила", "code": "rail_future_day"})
    assert_no_open_ai_session(order)
    mismatch = rail_bags_mismatch(order, wagons)
    if mismatch:
        raise ValidationError({
            "detail": f"Мешки не совпадают — {mismatch}. Поправьте заказ и отгрузите снова.",
            "code": "rail_bags_mismatch",
        })

    shipment, _ = Shipment.objects.get_or_create(order=order)
    shipment.bags_loaded = sum(wagon.bags for wagon in wagons)
    for position, wagon in enumerate(wagons, start=1):
        ShipmentWagon.objects.create(
            shipment=shipment,
            number=wagon.number,
            product=wagon.product,
            bags=wagon.bags,
            weight_kg=wagon.weight_kg,
            position=position,
        )
    station = " ".join(station.split())[:120]
    if station:
        # Пустая станция отчёта не стирает станцию, внесённую в заказ заранее.
        order.rail_station = station
        order.save(update_fields=["rail_station"])
    label = f"Вагоны отгружены по отчёту: {rail_phrase(len(wagons), order.rail_station)}"
    return _do_ship(
        order, shipment, user, label,
        shipped_at=backdate_moment(shipped_day) if shipped_day < today else None,
    )
