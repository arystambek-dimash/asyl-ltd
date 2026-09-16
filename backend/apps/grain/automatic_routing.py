"""Book saved scale events by an explicitly read plate and direction.

The model supplies identity only. Weights, timestamps and tare references always
come from committed physical measurements; every write shares the lane mutex.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from . import historical_tare, services, statuses as st
from .models import PassageScaleAutomationState, UnassignedWeighing, Wagon, WeighingIdentityCheck

# A truck re-weighed at the gate within this window is still the same visit
# (it turned around before loading). Later than that it has had time to load
# and leave, so a new front weighing means the previous exit went unseen.
REENTRY_GAP = timedelta(minutes=30)
# Rear plates are read with doubled letters or one swapped digit; up to this
# many edits still identifies the standing truck's own missed exit.
NEAR_PLATE_EDITS = 2


def _earlier_entry_pending(item, number):
    from .weighing_photos import photo_delivery_status
    earlier = UnassignedWeighing.objects.filter(
        status="open", stable_weight_at__lt=item.stable_weight_at,
        stable_weight_at__gte=timezone.now()-timedelta(hours=24),
        camera=item.camera, scale_number=item.scale_number,
        orientation__in=["front", "", "unknown"],
    ).filter(Q(vehicle_number="") | Q(vehicle_number=number)).select_related("identity_check").order_by("stable_weight_at")
    for entry in earlier[:100]:
        check = getattr(entry, "identity_check", None)
        if check and check.status in {"review", "matched"} and check.reason != "previous_exit_missing":
            continue
        if check and check.reason == "daily_budget_exhausted":
            continue
        if not entry.photo and photo_delivery_status(entry) == "unavailable":
            continue
        # Processing is bounded by photo retry limits and GPT attempt limits.
        # Terminal unreadable entries do not block unrelated traffic forever.
        return True
    return False


def _plate_distance(left, right, *, limit=NEAR_PLATE_EDITS):
    """Bounded Levenshtein distance between two plates (7-8 characters)."""
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for row, a in enumerate(left, 1):
        current = [row]
        for column, b in enumerate(right, 1):
            current.append(min(previous[column] + 1, current[column - 1] + 1, previous[column - 1] + (a != b)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


# A truck leaves loaded. An exit lighter than this above the entry weight is
# no evidence that a plate read at the rear belongs to that visit.
MIN_LOADED_GAIN_KG = 1000


def _plausible_exit(visit, item):
    entry_at = services._passage_entry_at(visit)
    return (
        visit.status == st.AT_SILO and visit.gross_weight_kg is not None and visit.tare_weight_kg is None
        and entry_at is not None and entry_at < item.stable_weight_at
        and item.weight_kg >= visit.gross_weight_kg + MIN_LOADED_GAIN_KG
    )


def trusted_exit(item):
    """A rear OCR plate whose truck is on site and leaves plausibly heavier needs no model check."""
    number = services.normalize_passage_number(item.vehicle_number)
    if not number or not services.KZ_VEHICLE_PLATE_RE.fullmatch(number):
        return False
    visits = list(Wagon.objects.filter(direction=Wagon.PASSAGE, number=number, status__in=st.ON_SITE_STATUSES)[:2])
    return len(visits) == 1 and _plausible_exit(visits[0], item)


def _similar_open_visits(number):
    """On-site visits whose plate is within NEAR_PLATE_EDITS of the read one, locked for booking."""
    visits = Wagon.objects.select_for_update().filter(
        direction=Wagon.PASSAGE, status__in=st.ON_SITE_STATUSES,
    ).exclude(number="").exclude(number=number)
    return [visit for visit in visits if _plate_distance(number, visit.number) <= NEAR_PLATE_EDITS]


def _open_visit(number):
    """The newest open visit of this plate.

    Two open visits can only mean the older one's exit was missed (a re-entry
    opened the newer one); the truck on the scale now belongs to the newest.
    """
    return (
        Wagon.objects.select_for_update()
        .filter(direction=Wagon.PASSAGE, number=number, status__in=st.ON_SITE_STATUSES)
        .annotate(entered=Coalesce("silo_arrived_at", "arrived_at"))
        .order_by(F("entered").desc(nulls_last=True), "-pk")
        .first()
    )


def _unseen_departure(item, wagon, number):
    """Did the standing truck leave unseen before this second front weighing?

    Returns ``("exit", weighing)`` when exactly one heavier rear weighing parked
    between the visit's entry and this one reads as this plate within
    NEAR_PLATE_EDITS: that is the missed exit. ``("unknown", None)`` when the
    truck stood longer than REENTRY_GAP without such a candidate: it left, but
    its exit needs the operator. ``("", None)`` for a quick re-weigh at the gate.
    """
    entry_at = services._passage_entry_at(wagon)
    parked = UnassignedWeighing.objects.filter(
        status=UnassignedWeighing.OPEN, scale_number=item.scale_number,
        orientation__in=["rear", "", "unknown"],
        stable_weight_at__gt=entry_at, stable_weight_at__lt=item.stable_weight_at,
        weight_kg__gt=wagon.gross_weight_kg,
    ).exclude(pk=item.pk).order_by("stable_weight_at")
    matches = [
        row for row in parked[:100]
        if row.vehicle_number and _plate_distance(number, row.vehicle_number) <= NEAR_PLATE_EDITS
    ]
    if len(matches) == 1:
        return "exit", matches[0]
    if item.stable_weight_at - entry_at >= REENTRY_GAP:
        return "unknown", None
    return "", None


def _recover_exit(wagon, departure, number):
    """Close the previous visit with the rear weighing its re-entry revealed."""
    departure = services.assign_unassigned_weighing(departure, wagon, None)
    WeighingIdentityCheck.objects.filter(weighing=departure).update(
        status="matched", reason="automatic_exit", lease_until=None,
    )
    if departure.capture_id:
        departure.capture.__class__.objects.filter(pk=departure.capture_id).update(wagon_id=wagon.pk, action="exit")
    services._log(
        wagon, "automatic_binding",
        f"Вывоз {number}: выезд восстановлен по повторному заезду, на выезде номер прочитан как {departure.vehicle_number}",
        None, auto=True, unassigned_id=departure.pk, orientation="rear",
        weight_kg=departure.weight_kg, occurred_at=departure.stable_weight_at.isoformat(),
    )


@transaction.atomic
def book(item, number, orientation):
    PassageScaleAutomationState.objects.select_for_update().get_or_create(scale_number="truck")
    item = UnassignedWeighing.objects.select_for_update().get(pk=item.pk)
    if item.status == UnassignedWeighing.ASSIGNED:
        return item
    if item.status != UnassignedWeighing.OPEN:
        raise ValueError("weighing_changed")
    if not services.KZ_VEHICLE_PLATE_RE.fullmatch(number):
        raise ValueError("plate_unreadable")
    if orientation not in {"front", "rear"}:
        raise ValueError("orientation_unknown")
    # Preserve the first OCR in identity-check audit; store the verified identity
    # on the event itself so all later matching and the UI use the same plate.
    item.vehicle_number, item.orientation = number, orientation
    item.save(update_fields=["vehicle_number", "orientation"])
    wagon = _open_visit(number)
    if orientation == "rear" and _earlier_entry_pending(item, number):
        raise ValueError("earlier_entry_pending")
    if orientation == "front":
        if wagon is not None and wagon.status == st.AT_SILO and wagon.tare_weight_kg is None:
            entry_at = services._passage_entry_at(wagon)
            if entry_at is None or item.stable_weight_at <= entry_at:
                raise ValueError("passage_time_conflict")
            verdict, departure = _unseen_departure(item, wagon, number)
            if verdict == "exit":
                _recover_exit(wagon, departure, number)
                wagon = None
            elif verdict == "unknown":
                # The open visit keeps its own entry; this weighing waits in the
                # review queue until the operator attaches that visit's exit,
                # then books itself as the next visit (see _earlier_entry_pending).
                raise ValueError("previous_exit_missing")
        if wagon is None:
            # Direct creation intentionally avoids the manual-action lane fence.
            wagon = Wagon.objects.create(
                direction=Wagon.PASSAGE, workflow="simple", number=number,
                status=st.ARRIVED, arrived_at=item.stable_weight_at,
                number_source="camera", number_camera_source=item.camera,
                cargo_name=settings.VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME,
            )
        if wagon.status == st.AT_SILO and wagon.tare_weight_kg is None:
            # A fresh, distinct front weighing of the same visit updates its tare.
            wagon.gross_weight_kg = services._record_weighing(
                wagon, "gross", item.weight_kg, None,
                occurred_at=item.stable_weight_at, **services._unassigned_scale_kwargs(item),
            )
            wagon.silo_arrived_at = item.stable_weight_at
            wagon.unloading_started_at = item.stable_weight_at
            wagon.save(update_fields=["gross_weight_kg", "silo_arrived_at", "unloading_started_at"])
            services._move_unassigned_photo(item, wagon, "gross")
            item.status, item.action, item.wagon = UnassignedWeighing.ASSIGNED, "entry", wagon
            item.resolved_at = timezone.now()
            item.save(update_fields=["status", "action", "wagon", "resolved_at"])
        elif wagon.status == st.ARRIVED and wagon.gross_weight_kg is None:
            item = services.assign_unassigned_weighing(item, wagon, None)
        else:
            raise ValueError("passage_state_mismatch")
    elif wagon is not None:
        entry_at = services._passage_entry_at(wagon)
        if wagon.status != st.AT_SILO or wagon.gross_weight_kg is None or wagon.tare_weight_kg is not None:
            raise ValueError("entry_weight_required")
        if entry_at is None or item.stable_weight_at <= entry_at:
            raise ValueError("passage_time_conflict")
        if item.weight_kg <= wagon.gross_weight_kg:
            raise ValueError("exit_weight_not_greater")
        item = services.assign_unassigned_weighing(item, wagon, None)
    else:
        similar = _similar_open_visits(number)
        nearest = [visit for visit in similar if _plate_distance(number, visit.number) <= 1 and _plausible_exit(visit, item)]
        if len(nearest) == 1:
            # Rear plates come back with a doubled letter or one digit off. The
            # one truck on site whose plate is a single edit away, entered
            # earlier and leaves loaded, is the truck in front of the camera.
            wagon = nearest[0]
            item = services.assign_unassigned_weighing(item, wagon, None)
            services._log(
                wagon, "automatic_binding",
                f"Вывоз {wagon.number}: выезд привязан по похожему номеру, камера прочитала {number}",
                None, auto=True, unassigned_id=item.pk, orientation="rear", read_number=number,
                weight_kg=item.weight_kg, occurred_at=item.stable_weight_at.isoformat(),
            )
        elif similar:
            # A trip completed from history under a misread spelling would hide
            # the real visit still open under the right one.
            raise ValueError("similar_visit_open")
        else:
            source = historical_tare.latest_before(item, number)
            if source is None:
                raise ValueError("saved_tare_missing")
            if item.weight_kg <= source.weight_kg:
                raise ValueError("exit_weight_not_greater")
            item = historical_tare.complete(
                item, None, reference_record=source.pk, number=number,
                reason="Автоматический выезд: последняя подтверждённая тара по госномеру", automatic=True,
            )
    if item.capture_id:
        item.capture.__class__.objects.filter(pk=item.capture_id).update(wagon_id=item.wagon_id, action=item.action)
    services._log(
        item.wagon, "automatic_binding",
        f"Вывоз {item.wagon.number or number}: автоматический {'заезд' if item.action == 'entry' else 'выезд'}",
        None, auto=True, unassigned_id=item.pk, orientation=orientation,
        weight_kg=item.weight_kg, occurred_at=item.stable_weight_at.isoformat(),
    )
    return item
