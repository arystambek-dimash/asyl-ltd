"""Import wagon stops from the wagon-scale collector into simple intake trips.

One stop = one wagon under the arch: arrival opens (or continues) a trip and
records the full weight; the departure records the empty weight after a
grace period, so a wagon that was only re-positioned keeps its trip.

``open_trip``, ``apply_entry`` and ``apply_departure`` mutate a trip and must
be called inside ``transaction.atomic()``; ``apply_departure`` opens its own
transaction around the exit weighing.
"""

import logging
import os
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, IntegrityError, models, transaction
from django.db.transaction import TransactionManagementError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from weighbridge.outbox import Outbox

from . import services, statuses as st, weighing_photos
from .models import Wagon, WagonArchStop, WeighingPhotoDelivery
from .outbox_importer import _store_evidence

log = logging.getLogger(__name__)

RUNTIME_CACHE_KEY = "grain:wagon-arch:runtime:v1"
RUNTIME_CACHE_SECONDS = 120
SCALE_KEY = "wagon"
# Код сервиса «выход не ниже входа» — в журнале рейса это отдельная причина.
EXIT_CODE_MAP = {"bad_tare": "exit_not_lower"}
# Причины, которые сами не рассосутся: автоматике здесь больше делать нечего,
# стоп уходит в ATTENTION к оператору и не перебирается каждый тик.
TERMINAL_REASONS = frozenset(
    {"not_simple_flow", "wrong_scale_action", "wagon_deleted", "import_error"}
)
MANUAL_DETAIL = "записано вручную"
# Часы сборщика могут немного уйти вперёд; вес «из будущего» — испорченное тело.
MAX_CLOCK_SKEW = timedelta(minutes=5)


def directory() -> Path:
    return Path(os.environ.get("WEIGHBRIDGE_WAGON_OUTBOX_DIR", "/var/lib/weighbridge-wagon"))


def enabled() -> bool:
    return bool(settings.WAGON_ARCH_AUTOMATION_ENABLED) and directory().is_dir()


def _aware(value, *, field="timestamp"):
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or timezone.is_naive(parsed):
        raise ValueError(f"wagon event {field} must be an aware ISO datetime")
    # Как в грузовом импортёре: измерение не может быть из будущего.
    if parsed > timezone.now() + MAX_CLOCK_SKEW:
        raise ValueError(f"wagon event {field} is in the future")
    return parsed


def _decimal(value):
    """Тела, восстановленные после рестарта сборщика, могут не иметь ключа."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def _error_code(exc) -> tuple[str, str]:
    detail = exc.detail if isinstance(exc, ValidationError) else {}
    if not isinstance(detail, dict):
        return "invalid_wagon_transition", str(exc)[:300]
    code = str(detail.get("code", "")) or "invalid_wagon_transition"
    return code, str(detail.get("detail", ""))[:300]


def _mark(stop, *, reason, detail, status=None):
    """Записать причину и (при необходимости) статус — только если что-то изменилось.

    Тик повторяется каждые 2—5 секунд: одинаковая причина не должна снова и
    снова переписывать строку (и двигать ``updated_at``, на который смотрит UI).
    """
    fields = []
    if stop.blocked_reason != reason:
        stop.blocked_reason = reason
        fields.append("blocked_reason")
    if stop.blocked_detail != detail:
        stop.blocked_detail = detail
        fields.append("blocked_detail")
    if status is not None and stop.status != status:
        stop.status = status
        fields.append("status")
    if fields:
        stop.save(update_fields=[*fields, "updated_at"])


def _blocked(stop, exc, *, code_map=None):
    """Причина блокировки на стопе; невосстановимая — сразу к оператору."""
    code, message = _error_code(exc)
    code = (code_map or {}).get(code, code)
    terminal = WagonArchStop.ATTENTION if code in TERMINAL_REASONS else None
    _mark(stop, reason=code, detail=message, status=terminal)


def _terminal(stop, *, reason, detail):
    _mark(stop, reason=reason, detail=detail, status=WagonArchStop.ATTENTION)


def _clear_block(stop, *, detail=""):
    _mark(stop, reason="", detail=detail)


def _wagon_of(stop):
    """Живой рейс стопа или None, если его успели удалить из CRM."""
    if stop.wagon_id is None:
        return None
    return Wagon.objects.filter(pk=stop.wagon_id).first()


def _wagon_deleted(stop):
    """Рейс исчез — стоп терминальный, автоматике здесь делать нечего."""
    if stop.wagon_id is not None:
        stop.wagon = None
        stop.save(update_fields=["wagon", "updated_at"])
    _terminal(stop, reason="wagon_deleted", detail="Рейс удалён")


# ── Прибытие ────────────────────────────────────────────────────────────────


def _previous_pending(stop):
    """Последний более ранний стоп, отъезд которого ещё не применён.

    Пересдача возможна лишь вскоре после отъезда: стоп, уехавший задолго до
    льготного окна, уже не может «поймать» новый вагон под аркой.
    """
    horizon = stop.arrived_at - timedelta(seconds=2 * settings.WAGON_ARCH_EXIT_GRACE_SECONDS)
    return (
        WagonArchStop.objects.select_for_update()
        .filter(
            arrived_at__lt=stop.arrived_at,
            departure_id__isnull=False,
            exit_applied_at__isnull=True,
            status=WagonArchStop.OPEN,
        )
        .filter(models.Q(departed_at__isnull=True) | models.Q(departed_at__gte=horizon))
        .order_by("-arrived_at", "-id")
        .first()
    )


def _weight_consistent(stop, previous) -> bool:
    """Вес не вырос — под аркой тот же вагон, лишь переставленный."""
    return (
        previous.exit_weight_kg is None
        or stop.full_weight_kg <= previous.exit_weight_kg + settings.WAGON_ARCH_NEXT_WAGON_RISE_KG
    )


def _continues(stop, previous) -> bool:
    """Тот же номер и вес не вырос — вагон лишь переставили под аркой.

    Нераспознанный номер не повод закрыть рейс: если вес сходится, это тот же
    вагон (сборщик просто не прочитал табличку с новой позиции).
    """
    if previous is None or not previous.wagon_id or not _weight_consistent(stop, previous):
        return False
    if stop.number:
        return stop.number == previous.number
    return True


def open_trip(stop):
    """Привязать стоп к рейсу: ожидаемый по номеру, продолженный или голый."""
    previous = _previous_pending(stop)
    if _continues(stop, previous):
        previous.status = WagonArchStop.SUPERSEDED
        previous.save(update_fields=["status", "updated_at"])
        stop.wagon = previous.wagon
        # Корень цепочки: входной вес и кадр рейса берутся только оттуда.
        stop.continues = previous.continues or previous
        stop.entry_applied_at = previous.entry_applied_at
        stop.opened_wagon_id = stop.wagon_id
        stop.save(update_fields=[
            "wagon", "opened_wagon_id", "continues", "entry_applied_at", "updated_at",
        ])
        message = (
            f"Вагон {stop.number} переставлен под аркой: рейс продолжается"
            if stop.number
            else "Вагон переставлен под аркой: номер не распознан, "
                 "рейс продолжен по весу"
        )
        services._log(
            stop.wagon, "arch", message, None, stop_id=str(stop.stop_id), auto=True,
        )
        return stop
    if previous is not None:
        # Предыдущий рейс закрывается до открытия следующего.
        apply_departure(previous, force=True)
    if stop.number:
        expected = (
            Wagon.objects.select_for_update()
            .filter(direction=Wagon.INTAKE, number=stop.number, status=st.EXPECTED)
            .order_by("id")
            .first()
        )
        if expected is not None:
            wagon = services._arrive_expected_wagon(expected, None, stop.camera)
            # Сервис ставит время «сейчас»; время рейса должно равняться стопу.
            wagon.arrived_at = stop.arrived_at
            wagon.number_source = "camera"
            wagon.number_camera_source = stop.camera
            wagon.save(update_fields=["arrived_at", "number_source", "number_camera_source"])
            stop.wagon = wagon
            stop.opened_wagon_id = wagon.pk
            stop.save(update_fields=["wagon", "opened_wagon_id", "updated_at"])
            return stop
        if Wagon.objects.filter(
            direction=Wagon.INTAKE, number=stop.number, status__in=st.ON_SITE_STATUSES
        ).exists():
            raise ValidationError(
                {"detail": f"Вагон {stop.number} уже на территории", "code": "wagon_on_site"}
            )
    wagon = Wagon.objects.create(
        supply=None, number=stop.number, direction=Wagon.INTAKE, workflow="simple",
        status=st.ARRIVED, arrived_at=stop.arrived_at,
        number_source="camera", number_camera_source=stop.camera,
    )
    services._log(
        wagon, "arrival",
        f"Вагон встал под арку: полный вес {stop.full_weight_kg} кг"
        + (f", номер {stop.number}" if stop.number else ", номер не распознан — укажите его вручную"),
        None, stop_id=str(stop.stop_id), camera_source=stop.camera, auto=True,
    )
    stop.wagon = wagon
    stop.opened_wagon_id = wagon.pk
    stop.save(update_fields=["wagon", "opened_wagon_id", "updated_at"])
    return stop


def entry_root(stop):
    """Стоп, чей вес и кадр и есть вход рейса.

    Вагон могли переставить под аркой несколько раз: вес второй и последующих
    стоянок — это середина выгрузки, брутто рейса задаёт только первая.
    """
    root = stop
    seen = {stop.pk}
    while root.continues_id is not None and root.continues_id not in seen:
        seen.add(root.continues_id)
        parent = root.continues
        if parent is None:
            break
        root = parent
    return root


def apply_entry(stop):
    """Записать входной вес. Без силоса приход ждёт оператора."""
    if stop.entry_applied_at is not None and stop.wagon_id is not None:
        return
    wagon = _wagon_of(stop)
    if wagon is None:
        # Рейс либо ещё не открыт (тогда нам сюда не попасть), либо удалён.
        _wagon_deleted(stop)
        return
    if stop.entry_applied_at is not None:
        return
    if wagon.gross_weight_kg is not None:
        # Оператор успел взвесить рейс руками — второй записи быть не должно.
        stop.entry_applied_at = timezone.now()
        stop.save(update_fields=["entry_applied_at", "updated_at"])
        _clear_block(stop, detail=MANUAL_DETAIL)
        return
    if services.assign_default_silo(wagon) is None and not wagon.assigned_silo_id:
        raise ValidationError({"detail": "Для прихода не назначен силос", "code": "silo_required"})
    wagon.refresh_from_db()
    root = entry_root(stop)
    services.record_simple_entry_weight(
        wagon, root.full_weight_kg, None,
        source="scale", scale_number=SCALE_KEY, scale_age_seconds=root.scale_age_seconds,
        scale_updated_at=root.scale_updated_at, occurred_at=root.arrived_at,
        photo_request_id=root.photo_request_id, photo_camera=root.camera,
    )
    delivery = WeighingPhotoDelivery.objects.filter(request_id=root.photo_request_id).first()
    if delivery is not None:
        # Кадр уже сохранён при импорте; здесь он привязывается к взвешиванию.
        weighing_photos._link_photo(delivery)
    stop.entry_applied_at = timezone.now()
    stop.save(update_fields=["entry_applied_at", "updated_at"])


def _open_and_apply(stop) -> bool:
    """Открыть рейс и записать вход. True — вход применился именно сейчас.

    Две отдельные транзакции: заблокированный вход (например «назначьте
    силос») не имеет права откатить уже открытый рейс — иначе каждый тик
    создавал бы и выбрасывал новый вагон.
    """
    if stop.wagon_id is None:
        if stop.opened_wagon_id is not None:
            # Рейс был и его удалили: открывать новый вместо него нельзя.
            _wagon_deleted(stop)
            return False
        try:
            with transaction.atomic():
                open_trip(stop)
        except ValidationError as exc:
            _blocked(stop, exc)
            return False
    try:
        with transaction.atomic():
            apply_entry(stop)
    except ValidationError as exc:
        _blocked(stop, exc)
        return False
    if stop.blocked_detail != MANUAL_DETAIL:
        _clear_block(stop)
    return stop.entry_applied_at is not None


def _import_arrival(event):
    key = UUID(event["id"])
    stop = WagonArchStop.objects.filter(stop_id=key).first()
    if stop is not None:
        return stop
    weight = event.get("weight_kg")
    if type(weight) is not int or weight <= 0 or weight > settings.TRUCK_SCALE_MAX_WEIGHT_KG:
        raise ValueError("wagon stop weight out of range")
    arrived_at = _aware(event["stable_weight_at"], field="stable_weight_at")
    try:
        with transaction.atomic():
            stop = WagonArchStop.objects.create(
                stop_id=key, camera=event["camera"], arrived_at=arrived_at,
                full_weight_kg=weight,
                scale_age_seconds=_decimal(event.get("scale_age_seconds")),
                scale_updated_at=event.get("scale_updated_at") or "",
                still_seconds=_decimal(event.get("still_seconds")),
                number=(event.get("number") or "").strip(),
                number_source=event.get("number_source") or "",
                recognition_error=event.get("recognition_error") or "",
                ocr_attempts=int(event.get("ocr_attempts") or 0),
                photo_request_id=key,
            )
            delivery, _ = WeighingPhotoDelivery.objects.get_or_create(
                request_id=key, defaults={"camera": stop.camera}
            )
            _store_evidence(delivery, event)
    except IntegrityError:
        # Пересекающиеся итерации импортёра: стоп уже создан — он и есть результат.
        log.warning("Стоп вагона %s уже импортирован параллельно", key)
        existing = WagonArchStop.objects.filter(stop_id=key).first()
        if existing is None:
            raise
        return existing
    _open_and_apply(stop)
    return stop


# ── Отъезд ──────────────────────────────────────────────────────────────────


def _import_departure(event):
    departure_id = UUID(event["id"])
    stop = WagonArchStop.objects.filter(stop_id=UUID(event["stop_id"])).first()
    if stop is None:
        raise ValueError("departure refers to an unknown stop")
    if stop.departure_id == departure_id:
        return stop
    if stop.departure_id is not None:
        log.warning("Second departure %s for stop %s ignored", departure_id, stop.stop_id)
        return stop
    weight = event.get("weight_kg")
    stop.departure_id = departure_id
    stop.exit_weight_kg = int(weight) if type(weight) is int and weight > 0 else None
    stop.exit_stable_at = (
        _aware(event["stable_weight_at"], field="stable_weight_at")
        if event.get("stable_weight_at") else None
    )
    stop.departed_at = _aware(event["departed_at"], field="departed_at")
    stop.motion_gap = bool(event.get("motion_gap"))
    try:
        with transaction.atomic():
            stop.save(update_fields=[
                "departure_id", "exit_weight_kg", "exit_stable_at", "departed_at",
                "motion_gap", "updated_at",
            ])
    except IntegrityError:
        # Этот же отъезд уже записан параллельной итерацией.
        log.warning("Отъезд %s уже импортирован параллельно", departure_id)
        stop.refresh_from_db()
    return stop


def apply_departure(stop, *, force=False, now=None):
    """Записать выходной вес, когда пересдача под аркой уже исключена."""
    now = now or timezone.now()
    if stop.departure_id is None or stop.exit_applied_at is not None or stop.status != WagonArchStop.OPEN:
        return
    if stop.entry_applied_at is None:
        return  # выезд ждёт за заблокированным приходом
    if not force and stop.departed_at + timedelta(seconds=settings.WAGON_ARCH_EXIT_GRACE_SECONDS) > now:
        return
    wagon = _wagon_of(stop)
    if wagon is None:
        _wagon_deleted(stop)
        return
    if wagon.tare_weight_kg is not None or wagon.status in (st.COMPLETED, st.WEIGHT_DISCREPANCY):
        # Оператор закрыл рейс руками — автоматике нечего дописывать.
        _mark(stop, reason="", detail=MANUAL_DETAIL, status=WagonArchStop.CLOSED)
        return
    if stop.motion_gap:
        # Отъезд не был виден (рестарт сборщика / выпавшая камера): вес мог
        # быть снят посреди выгрузки, автоматом такой выезд не пишем.
        _terminal(
            stop, reason="exit_unseen",
            detail="Отъезд не был виден — подтвердите вес выезда",
        )
        return
    if stop.exit_weight_kg is None:
        _terminal(
            stop, reason="no_exit_weight",
            detail="Перед отъездом не было устойчивого веса",
        )
        return
    try:
        with transaction.atomic():
            wagon = Wagon.objects.select_for_update(of=("self",)).get(pk=stop.wagon_id)
            services.record_simple_exit_weight(
                wagon, stop.exit_weight_kg, None,
                source="scale", scale_number=SCALE_KEY, scale_age_seconds=None,
                scale_updated_at="", occurred_at=stop.exit_stable_at or stop.departed_at,
            )
            # Расхождение веса — обычный разбор оператора в CRM, а не ошибка
            # импорта: стоп закрыт, потому что выходной вес записан. Закрытие
            # и само взвешивание — одна транзакция: иначе сбой записи оставил
            # бы вес в рейсе при открытом стопе и выезд применился бы дважды.
            stop.exit_applied_at = now
            stop.status = WagonArchStop.CLOSED
            stop.blocked_reason = stop.blocked_detail = ""
            stop.save(update_fields=[
                "exit_applied_at", "status", "blocked_reason", "blocked_detail", "updated_at",
            ])
    except ValidationError as exc:
        # Отказ сервиса на выезде всегда к оператору: одна запись, не две.
        code, message = _error_code(exc)
        _mark(
            stop, reason=EXIT_CODE_MAP.get(code, code), detail=message,
            status=WagonArchStop.ATTENTION,
        )
        return
    except Wagon.DoesNotExist:
        _wagon_deleted(stop)
        return


# ── Оркестрация ─────────────────────────────────────────────────────────────


def import_event(event):
    if not isinstance(event, dict):
        raise ValueError("wagon event must be an object")
    if event.get("version") != 2:
        raise ValueError("unsupported wagon event version")
    try:
        UUID(str(event["id"]))
    except (KeyError, ValueError, AttributeError, TypeError) as exc:
        raise ValueError("wagon event id must be a UUID") from exc
    kind = event.get("kind")
    if kind == "wagon_stop":
        return _import_arrival(event)
    if kind == "wagon_departure":
        return _import_departure(event)
    raise ValueError(f"unknown wagon event kind: {kind!r}")


def _guarded(stop, work) -> bool:
    """Выполнить работу по одному стопу, не роняя общий тик монитора.

    Импортёр живёт в процессе монитора вместе с грузовым конвейером: любая
    неожиданная ошибка по одному стопу паркует этот стоп у оператора, а цикл
    продолжается.

    Сбои БД — исключение из этого правила: они не свойство стопа, а свойство
    окружения. Запарковать такой стоп значило бы навсегда пометить исправный
    рейс терминальным `import_error` из-за одного оборванного соединения,
    поэтому `DatabaseError` летит наружу — к логгеру монитора и healthcheck,
    которые умеют это пережить и повторить.
    """
    try:
        return bool(work(stop))
    except (DatabaseError, TransactionManagementError):
        raise
    except Exception as exc:  # noqa: BLE001 — намеренный конверт вокруг одного стопа
        log.exception("Стоп вагона %s не удалось обработать", stop.stop_id)
        _terminal(stop, reason="import_error", detail=str(exc)[:300])
        return False


def apply_pending(*, now=None):
    """Повторить заблокированные приходы и применить созревшие отъезды.

    Рассчитано на один импортёр за раз (монитор Части 4 не должен запускать
    несколько тиков параллельно): порядок стопов и блокировки строк здесь
    предполагают единственного писателя.
    """
    now = now or timezone.now()
    retried = applied = 0
    pending_entries = WagonArchStop.objects.filter(
        status=WagonArchStop.OPEN, entry_applied_at__isnull=True,
    ).order_by("arrived_at")
    for stop in pending_entries:
        if _guarded(stop, _open_and_apply):
            retried += 1
    departures = WagonArchStop.objects.filter(
        status=WagonArchStop.OPEN, departure_id__isnull=False, exit_applied_at__isnull=True,
    ).order_by("arrived_at")
    for stop in departures:
        # Приезд следующего вагона доказывает, что пересдачи уже не будет.
        later_exists = WagonArchStop.objects.filter(arrived_at__gt=stop.arrived_at).exists()

        def _apply(target, *, force=later_exists, before=stop.exit_applied_at):
            apply_departure(target, force=force, now=now)
            target.refresh_from_db()
            return target.exit_applied_at != before

        if _guarded(stop, _apply):
            applied += 1
    return {"entries_retried": retried, "departures_applied": applied}


def dismiss(stop, user=None):
    """Оператор разобрался сам: стоп закрыт и больше не ждёт автоматики.

    Это решение человека, а не автоматики, поэтому оно попадает в журнал рейса
    с его именем (``auto=False``). У стопа без рейса писать некуда — такой
    случай тоже закрывается, просто молча.
    """
    if stop.status == WagonArchStop.CLOSED:
        return stop
    _mark(stop, reason="", detail="закрыто оператором", status=WagonArchStop.CLOSED)
    wagon = _wagon_of(stop)
    if wagon is not None:
        services._log(
            wagon, "arch", f"Стоянка {stop.stop_id} закрыта оператором", user,
            stop_id=str(stop.stop_id), auto=False,
        )
    return stop


def _runtime_payload(box, *, imported, discarded):
    heartbeat = box.state("heartbeat") or {}
    last = WagonArchStop.objects.select_related("wagon").order_by("-arrived_at", "-id").first()
    return {
        "enabled": True,
        "camera": settings.WAGON_ARCH_CAMERA,
        "collector": {
            **box.counts(),
            "status": heartbeat.get("status", "starting"),
            "standing": heartbeat.get("standing"),
            "motion": heartbeat.get("motion"),
            "heartbeat_at": heartbeat.get("updated_at"),
        },
        "imported": imported,
        "discarded": discarded,
        "pending_stops": WagonArchStop.objects.filter(status=WagonArchStop.OPEN)
        .exclude(blocked_reason="").count(),
        "attention_stops": WagonArchStop.objects.filter(status=WagonArchStop.ATTENTION).count(),
        "last_stop": None if last is None else {
            "id": last.pk, "stop_id": str(last.stop_id), "number": last.number, "status": last.status,
            "full_weight_kg": last.full_weight_kg, "exit_weight_kg": last.exit_weight_kg,
            "arrived_at": last.arrived_at.isoformat(), "wagon_id": last.wagon_id,
            "blocked_reason": last.blocked_reason, "blocked_detail": last.blocked_detail,
        },
        "updated_at": timezone.now().isoformat(),
    }


def poll_once(*, limit=10):
    """Импортировать до `limit` событий. Нечитаемое тело не держит очередь."""
    box = Outbox(directory())
    imported = discarded = 0
    for _ in range(limit):
        event = box.next()
        if event is None:
            break
        try:
            import_event(event)
        except (ValueError, KeyError, TypeError) as exc:
            # Любое нечитаемое тело (в т.ч. без обязательного ключа) не должно
            # держать очередь: предупредить и подтвердить. Отъезд без стопа —
            # потеря данных сборщика, поэтому он логируется как ошибка.
            unknown_stop = str(exc) == "departure refers to an unknown stop"
            report = log.error if unknown_stop else log.warning
            report("Пропущено событие вагона %s: %r", event.get("id"), exc)
            box.ack(event.get("id"))
            discarded += 1
            continue
        box.ack(event["id"])
        imported += 1
    result = apply_pending()
    cache.set(
        RUNTIME_CACHE_KEY,
        _runtime_payload(box, imported=imported, discarded=discarded),
        RUNTIME_CACHE_SECONDS,
    )
    return {"imported": imported, "discarded": discarded, **result}


def runtime():
    """Снимок состояния импортёра для UI.

    ``imported``/``discarded`` — счётчики ПОСЛЕДНЕГО тика, а не сумма за смену:
    их смысл «что произошло только что». Снимок живёт в кэше
    ``RUNTIME_CACHE_SECONDS`` секунд, поэтому ``enabled`` и остальные поля
    могут отставать от реальности до 120 с (а после рестарта кэша до первого
    тика возвращается запасной payload, где счётчики нулевые).
    """
    payload = cache.get(RUNTIME_CACHE_KEY)
    if payload is None:
        return {
            "enabled": enabled(), "camera": settings.WAGON_ARCH_CAMERA, "collector": None,
            "imported": 0, "discarded": 0, "pending_stops": 0, "attention_stops": 0,
            "last_stop": None, "updated_at": None,
        }
    return payload
