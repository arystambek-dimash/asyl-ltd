"""Book saved scale events by an explicitly read plate and direction.

The model supplies identity only. Weights, timestamps and tare references always
come from committed physical measurements; every write shares the lane mutex.
"""
import logging
import re
from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.exceptions import APIException

from . import historical_tare, scale, services, statuses as st
from .models import PassageScaleAutomationState, UnassignedWeighing, VehicleTareMemory, Wagon, WeighingIdentityCheck

log = logging.getLogger(__name__)

# A truck re-weighed at the gate within this window is still the same visit
# (it turned around before loading). Later than that it has had time to load
# and leave, so a new front weighing means the previous exit went unseen.
REENTRY_GAP = timedelta(minutes=30)
# Rear plates are read with doubled letters or one swapped digit; up to this
# many edits still identifies the standing truck's own missed exit.
NEAR_PLATE_EDITS = 2
# An unread front weighing is this truck's entry only when it weighs what the
# truck weighed empty the last time its plate was read.
TARE_TOLERANCE_KG = 300


def _max_trip():
    """No visit lasts longer. A truck still open after this left unseen."""
    return timedelta(hours=settings.WEIGHING_AI_ENTRY_MAX_HOURS)


def _local(at):
    return timezone.localtime(at).strftime("%d.%m %H:%M")


def _earlier_entry_pending(item, number):
    from .weighing_photos import photo_delivery_status
    earlier = UnassignedWeighing.objects.filter(
        status="open", stable_weight_at__lt=item.stable_weight_at,
        stable_weight_at__gte=timezone.now()-timedelta(hours=24),
        camera=item.camera, scale_number=item.scale_number,
        orientation__in=["front", "", "unknown"],
    ).select_related("identity_check").order_by("stable_weight_at")
    for entry in earlier[:100]:
        # A rear plate one or two characters off still names the truck whose
        # own entry is waiting; an exact comparison would close an older visit.
        if entry.vehicle_number and _plate_distance(number, entry.vehicle_number) > NEAR_PLATE_EDITS:
            continue
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


def _plate_core(number):
    """The plate without the part a camera drops or invents.

    The two-digit region of a KZ plate (123ABC13 -> 123ABC) and the leading
    letter of the older form (E065CUA -> 065CUA): a front camera that keeps
    the core but loses the region opens the visit under E065CUA, and the
    same truck re-enters as 065CUA13. Any other layout is kept as it is.
    """
    if re.fullmatch(r"[0-9]{3}[A-Z]{2,3}[0-9]{2}", number):
        return number[:-2]
    if re.fullmatch(r"[A-Z][0-9]{3}[A-Z]{3}", number):
        return number[1:]
    return number


def _misspelled_open_visits(number):
    """On-site visits that may be this truck under a misread spelling, locked for booking.

    One edit away, or the same plate core. Two edits are a neighbour: 261BBF13
    and 411BBF13, 684BFE13 and 682BFA13 are on site together.
    """
    core = _plate_core(number)
    visits = Wagon.objects.select_for_update().filter(
        direction=Wagon.PASSAGE, status__in=st.ON_SITE_STATUSES,
    ).exclude(number="").exclude(number=number)
    return [visit for visit in visits if _plate_distance(number, visit.number) <= 1 or _plate_core(visit.number) == core]


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


# The identity worker claims parked weighings of the last day only
# (weighing_identity._claim); an older one it never picked up will not be read.
IDENTITY_WINDOW = timedelta(hours=24)


def _verdict_final(row):
    """The identity check of a parked weighing ended; no plate will still turn up on it.

    A check that is pending or retrying (frame not delivered, model backoff,
    daily budget) may yet read one: until it ends, a weighing without a plate
    is neither unread nor somebody else's. A weighing without any check is
    the same while the worker can still claim it, and unread for good once it
    is older than IDENTITY_WINDOW.
    """
    check = getattr(row, "identity_check", None)
    if check is None:
        return row.stable_weight_at < timezone.now() - IDENTITY_WINDOW
    return check.status == "review"


def _weak_plate_row(row):
    """The camera voted for this plate short of confirmation (two votes of three).

    While the weighing is still parked no check has confirmed it, so it is no
    reading: no stronger than the raw model text, and a pending check on it
    is a pending check on a plate-less weighing.
    """
    capture = row.capture if row.capture_id else None
    return capture is not None and (capture.ai_payload_json or {}).get("weak_plate") is True


def _read_plates(row):
    """Every plate anyone read on a parked weighing: OCR/verified and the raw model text.

    The raw reading may be in no valid format (region cut off, emblem kept), so
    it never books anything; it still tells a different truck from a plate
    nobody managed to read at all. A weak camera plate is left out (see
    ``_weak_plate_row``).
    """
    check = getattr(row, "identity_check", None)
    plates = {row.vehicle_number} if row.vehicle_number and not _weak_plate_row(row) else set()
    verdict = check.evidence.get("verdict", {}) if check else {}
    reading = verdict.get("exit", {}) if isinstance(verdict, dict) else {}
    raw = re.sub(r"[^A-Z0-9]", "", str(reading.get("plate") or "").upper()) if isinstance(reading, dict) else ""
    if raw:
        plates.add(raw)
    return plates


def _near_plate(number, plates):
    return any(_plate_distance(number, plate) <= NEAR_PLATE_EDITS for plate in plates)


def _missed_exit(wagon, number, *, at, scale_number, exclude_pk=None, unread_allowed=True):
    """The loaded rear weighings parked in the visit's window that may be its unseen exit.

    Returns ``(matches, unsettled)``. A candidate was parked between the
    visit's entry and ``at``, no later than the longest trip after the entry,
    and weighs at least MIN_LOADED_GAIN_KG more than the entry. It matches
    when it reads as this plate within NEAR_PLATE_EDITS, or (``unread_allowed``)
    when nobody read a plate on it at all (the rear camera finds no tag on
    almost half of the exits) and the truck had stood longer than REENTRY_GAP
    by ``at``: a truck that turned around at the gate within that gap cannot
    have loaded. ``unsettled`` is set while a plate-less candidate's own check
    has not ended: its frame may still yield a plate, this one or another
    truck's, so nothing is decided.
    """
    entry_at = services._passage_entry_at(wagon)
    stood = at - entry_at
    parked = UnassignedWeighing.objects.filter(
        status=UnassignedWeighing.OPEN, scale_number=scale_number,
        orientation__in=["rear", "", "unknown"],
        stable_weight_at__gt=entry_at, stable_weight_at__lt=min(at, entry_at + _max_trip()),
        weight_kg__gte=wagon.gross_weight_kg + MIN_LOADED_GAIN_KG,
    ).select_related("identity_check", "capture").order_by("stable_weight_at")
    if exclude_pk is not None:
        parked = parked.exclude(pk=exclude_pk)
    matches, unsettled = [], False
    for row in parked[:100]:
        plates = _read_plates(row)
        if plates:
            if _near_plate(number, plates):
                matches.append(row)
        elif not _verdict_final(row):
            unsettled = True
        elif unread_allowed and stood >= REENTRY_GAP:
            matches.append(row)
    return matches, unsettled


def _unseen_departure(item, wagon, number, *, unread_allowed=True):
    """Did the standing truck leave unseen before this second front weighing?

    Returns ``("exit", weighing)`` when exactly one candidate of
    ``_missed_exit`` is that truck and none is unsettled. ``("abandoned",
    None)`` when the truck stood longer than any trip lasts and there is no
    candidate at all: the visit is stale and ends without an exit.
    ``("unknown", None)`` when it stood longer than REENTRY_GAP and the
    candidates do not name it (several, or one still unchecked): it left, but
    its exit needs the operator. ``("", None)`` for a quick re-weigh at the gate.
    """
    stood = item.stable_weight_at - services._passage_entry_at(wagon)
    matches, unsettled = _missed_exit(
        wagon, number, at=item.stable_weight_at, scale_number=item.scale_number,
        exclude_pk=item.pk, unread_allowed=unread_allowed,
    )
    if len(matches) == 1 and not unsettled:
        return "exit", matches[0]
    if len(matches) > 1:
        # Two candidates name nobody: ambiguity, not a visit nobody closed.
        return "unknown", None
    if stood >= _max_trip() and not matches and not unsettled:
        return "abandoned", None
    if stood >= REENTRY_GAP:
        return "unknown", None
    return "", None


def _recover_exit(wagon, departure, number, *, trigger="по повторному заезду"):
    """Close the visit with the rear weighing that its re-entry, or the timer, revealed as its exit."""
    departure = services.assign_unassigned_weighing(departure, wagon, None)
    WeighingIdentityCheck.objects.update_or_create(
        weighing=departure, defaults={"status": "matched", "reason": "automatic_exit", "lease_until": None},
    )
    if departure.capture_id:
        departure.capture.__class__.objects.filter(pk=departure.capture_id).update(wagon_id=wagon.pk, action="exit")
    opened = f" (рейс был открыт под номером {wagon.number})" if wagon.number != number else ""
    read = (
        f"на выезде номер прочитан как {departure.vehicle_number}" if departure.vehicle_number
        else "номер на выезде не прочитан"
    )
    services._log(
        wagon, "automatic_binding",
        f"Вывоз {number}: выезд восстановлен {trigger}{opened}, {read}",
        None, auto=True, unassigned_id=departure.pk, orientation="rear",
        weight_kg=departure.weight_kg, occurred_at=departure.stable_weight_at.isoformat(),
    )


def _abandon_visit(wagon, item, number):
    """End the visit whose exit was never seen: its truck is back at the gate."""
    under = f" под номером {number}" if wagon.number != number else ""
    services._set_status(
        wagon, st.CANCELLED, None,
        f"Вывоз {wagon.number}: рейс закрыт без выезда — машина снова заехала {_local(item.stable_weight_at)}{under}, "
        "выезд не был зафиксирован",
        auto=True, unassigned_id=item.pk,
    )
    wagon.exit_note = "Выезд не зафиксирован: рейс закрыт автоматически при новом заезде"
    wagon.save(update_fields=["exit_note"])


def _expire_visit(wagon):
    """End the visit nobody closed: twice the longest trip has passed and no exit fits it."""
    services._set_status(
        wagon, st.CANCELLED, None,
        f"Вывоз {wagon.number}: рейс закрыт без выезда по сроку — "
        f"выезд не был зафиксирован за {2 * settings.WEIGHING_AI_ENTRY_MAX_HOURS} ч",
        auto=True,
    )
    wagon.exit_note = "Выезд не зафиксирован: рейс закрыт автоматически по сроку"
    wagon.save(update_fields=["exit_note"])


def _next_parked_entry(wagon, *, scale_number):
    """The earliest parked front weighing of this plate after the visit's entry: the truck came back.

    That re-entry waits as ``previous_exit_missing`` until this visit is
    settled and then opens the next visit, taking any later exit with it, so
    this visit's exit can only lie between its entry and the re-entry. A
    front plate read one or two characters off still names the truck.
    """
    # A re-entry is an empty truck: a loaded weighing whose direction stayed
    # unknown is still this visit's exit candidate, not the truck coming back.
    parked = UnassignedWeighing.objects.filter(
        status=UnassignedWeighing.OPEN, scale_number=scale_number,
        orientation__in=["front", "", "unknown"], stable_weight_at__gt=services._passage_entry_at(wagon),
        weight_kg__lt=wagon.gross_weight_kg + MIN_LOADED_GAIN_KG,
    ).exclude(vehicle_number="").order_by("stable_weight_at")
    for row in parked[:100]:
        if _plate_distance(wagon.number, row.vehicle_number) <= NEAR_PLATE_EDITS:
            return row
    return None


def _fits_another_visit(departure, wagon):
    """May this loaded rear weighing be the exit of an open visit other than ``wagon``?

    Then it names nobody: between two trucks the timer does not choose. Any
    open visit, stale or not, that entered within the longest trip before the
    weighing and is MIN_LOADED_GAIN_KG lighter fits it when nobody read a
    plate on the weighing or a read plate is within NEAR_PLATE_EDITS of its own.
    """
    plates = _read_plates(departure)
    others = (
        Wagon.objects.filter(direction=Wagon.PASSAGE, status=st.AT_SILO, gross_weight_kg__isnull=False, tare_weight_kg__isnull=True)
        .exclude(pk=wagon.pk)
        .annotate(entered=Coalesce("silo_arrived_at", "arrived_at"))
        .filter(
            entered__gt=departure.stable_weight_at - _max_trip(), entered__lt=departure.stable_weight_at,
            gross_weight_kg__lte=departure.weight_kg - MIN_LOADED_GAIN_KG,
        )
    )
    return any(not plates or _near_plate(visit.number, plates) for visit in others)


def _reconcile_candidates(wagon, now):
    """The stale visit's missed-exit candidates, looked for no later than its plate's re-entry."""
    at = now
    reentry = _next_parked_entry(wagon, scale_number=scale.TRUCK_SCALE_KEY)
    if reentry is not None:
        at = min(now, reentry.stable_weight_at)
    return _missed_exit(wagon, wagon.number, at=at, scale_number=scale.TRUCK_SCALE_KEY)


def _reconcile_visit(wagon, now, matches, unsettled, blocked):
    if unsettled or blocked or len(matches) > 1:
        return "left"
    if matches:
        _recover_exit(
            wagon, matches[0], wagon.number,
            trigger=f"по сроку (рейс старше {settings.WEIGHING_AI_ENTRY_MAX_HOURS} ч)",
        )
        return "closed"
    if now - services._passage_entry_at(wagon) >= 2 * _max_trip():
        _expire_visit(wagon)
        return "cancelled"
    return "left"


def reconcile_stale_visits(now=None):
    """Settle the visits open longer than any trip lasts, on a timer instead of a re-entry.

    A truck that does not come back (a one-off carrier, a plate the front
    camera invented) leaves its visit open for good, and an open visit parks
    every later weighing of its plate. Under the lane lock, each visit whose
    entry is older than the longest trip ends the way a re-entry would end
    it: the one unread loaded exit of its window closes it (``closed``); with
    no candidate at all and twice the longest trip gone it is cancelled
    without an exit (``cancelled``); several candidates, a candidate whose
    check has not ended, one that may as well be another open visit's exit,
    or a younger visit without one are left to the next run or the operator
    (``left``). The window ends at the plate's own parked re-entry when there
    is one: what came after belongs to the next visit. Candidates are
    gathered, and their exclusivity decided, for every stale visit before any
    is settled, so a visit that had one is never cancelled by age because this
    run booked it elsewhere, and a cancellation never frees a candidate. One
    visit's failure is logged and never stops the others.
    """
    now = now or timezone.now()
    counters = {"closed": 0, "cancelled": 0, "left": 0}
    with transaction.atomic():
        PassageScaleAutomationState.objects.select_for_update().get_or_create(scale_number=scale.TRUCK_SCALE_KEY)
        stale = (
            Wagon.objects.select_for_update()
            .filter(direction=Wagon.PASSAGE, status=st.AT_SILO, gross_weight_kg__isnull=False, tare_weight_kg__isnull=True)
            .exclude(number="")
            .annotate(entered=Coalesce("silo_arrived_at", "arrived_at"))
            .filter(entered__lt=now - _max_trip())
            .order_by("entered", "pk")
        )
        planned = []
        for wagon in stale:
            matches, unsettled = _reconcile_candidates(wagon, now)
            # Decided now, while every visit of this run is still open: a
            # candidate that may be another visit's exit stays nobody's even
            # after that visit is cancelled by age a few lines below.
            blocked = len(matches) == 1 and _fits_another_visit(matches[0], wagon)
            planned.append((wagon, matches, unsettled, blocked))
        for wagon, matches, unsettled, blocked in planned:
            try:
                with transaction.atomic():
                    counters[_reconcile_visit(wagon, now, matches, unsettled, blocked)] += 1
            except (ValueError, APIException, IntegrityError):
                log.exception("Stale visit %s (#%s) was left open: reconcile failed", wagon.number, wagon.pk)
                counters["left"] += 1
    return counters


def _settle_similar_visit(item, number):
    """A visit opened under a misread spelling of this plate ends when the truck re-enters.

    Its exit could never name it (the rear reading has no open visit under the
    real plate), so it would stay open forever. Real neighbours share the site,
    so only strong evidence names the one such visit as this truck: one edit or
    the same plate core, the same empty weight within TARE_TOLERANCE_KG, and an
    exit that was read as this plate (an unread exit may be anybody's). The
    time limit closes only a same-core spelling; a one-edit neighbour that
    stood too long is left to the operator. When neither an exit nor the time
    limit settles it, the new visit still opens under the plate read now.
    """
    similar = _misspelled_open_visits(number)
    if len(similar) != 1:
        return
    visit = similar[0]
    entry_at = services._passage_entry_at(visit)
    if (
        visit.status != st.AT_SILO or visit.gross_weight_kg is None or visit.tare_weight_kg is not None
        or entry_at is None or item.stable_weight_at <= entry_at
        or abs(item.weight_kg - visit.gross_weight_kg) > TARE_TOLERANCE_KG
    ):
        return
    verdict, departure = _unseen_departure(item, visit, number, unread_allowed=False)
    if verdict == "exit":
        _recover_exit(visit, departure, number)
    elif verdict == "abandoned" and _plate_core(visit.number) == _plate_core(number):
        # Only the same plate under another layout is this truck for certain;
        # a one-edit neighbour that stood too long is left to the operator.
        _abandon_visit(visit, item, number)


def _parked_entry(item, number):
    """The unread front weighing that was this truck's entry, when exactly one fits.

    The front camera missed the plate; the empty weight sits in the review
    queue while the exit reads fine. A tare copied from history at the moment
    of leaving would hide that real entry, so it is looked for first. Only the
    remembered empty weight of this plate tells its weighing from any other
    truck's: with no memory nothing is recognised.
    """
    memory = VehicleTareMemory.objects.filter(number=number).select_related("record").first()
    if memory is None:
        return None
    lower = item.stable_weight_at - _max_trip()
    upper = item.stable_weight_at - timedelta(seconds=settings.VEHICLE_PLATE_AUTO_EXPORT_MIN_TRIP_SECONDS)
    parked = UnassignedWeighing.objects.filter(
        status=UnassignedWeighing.OPEN, scale_number=item.scale_number, camera=item.camera,
        orientation__in=["front", "", "unknown"],
        stable_weight_at__gte=lower, stable_weight_at__lte=upper,
        weight_kg__lte=item.weight_kg - MIN_LOADED_GAIN_KG,
    ).exclude(pk=item.pk).select_related("identity_check", "capture").order_by("stable_weight_at")
    # Nobody read a plate, or what was read is this plate (a weak camera plate,
    # the model's raw text in a broken layout); a different plate rules the
    # weighing out, and so does a check that has not ended yet.
    fitting = []
    for row in parked[:100]:
        if not _verdict_final(row) or abs(row.weight_kg - memory.record.weight_kg) > TARE_TOLERANCE_KG:
            continue
        plates = _read_plates(row)
        if not plates or _near_plate(number, plates):
            fitting.append(row)
    return fitting[0] if len(fitting) == 1 else None


# A front plate the camera invented shares at least this many characters of
# the plate core with the real one (253ZOU for 532OUB: five, 065CUA for
# 065CUA: six); an unrelated plate on site shares fewer (237AAX and 853UVA:
# two, 132XYZ and 123ABC: three).
ORPHAN_PLATE_OVERLAP = 4


def _plate_overlap(left, right):
    """How many characters the cores of two plates share, with repetition.

    A misread keeps the characters, not their order. The region is left out:
    two strangers from one region share it, and it would carry 132XYZ13 and
    123ABC13 over the threshold.
    """
    return sum((Counter(_plate_core(left)) & Counter(_plate_core(right))).values())


def _orphan_visit(item, number):
    """The open visit under a misread front plate whose exit this is, when exactly one fits.

    The front camera invented a plate (253ZOU81 for 532OUB13): no exit can
    ever name that visit, and the truck's own exit, read fine at the rear,
    finds no open visit under the real plate and would take a tare from
    history while the phantom stays open for good. The visit is this truck's
    when the phantom spelling never left the site (no completed trip under
    it: a misreading, not a neighbour), its plate core shares ORPHAN_PLATE_OVERLAP
    characters with the real plate's, it entered within the longest trip
    before the exit, its empty weight is what this plate weighs empty
    (VehicleTareMemory, TARE_TOLERANCE_KG) and the exit leaves it plausibly
    heavier. Without tare memory nothing is recognised.
    """
    memory = VehicleTareMemory.objects.filter(number=number).select_related("record").first()
    if memory is None:
        return None
    visits = (
        Wagon.objects.select_for_update()
        .filter(direction=Wagon.PASSAGE, status=st.AT_SILO, gross_weight_kg__isnull=False, tare_weight_kg__isnull=True)
        .exclude(number="").exclude(number=number)
        .annotate(entered=Coalesce("silo_arrived_at", "arrived_at"))
        .filter(entered__gt=item.stable_weight_at - _max_trip(), entered__lt=item.stable_weight_at)
    )
    fitting = [
        visit for visit in visits
        if _plate_overlap(number, visit.number) >= ORPHAN_PLATE_OVERLAP
        and abs(visit.gross_weight_kg - memory.record.weight_kg) <= TARE_TOLERANCE_KG
        and _plausible_exit(visit, item)
        and not Wagon.objects.filter(direction=Wagon.PASSAGE, number=visit.number, status=st.COMPLETED).exists()
    ]
    return fitting[0] if len(fitting) == 1 else None


def _merge_orphan_visit(visit, number):
    """Put the visit under the plate its exit was read as; the phantom spelling and its tare memory go."""
    previous = visit.number
    wagon = services.set_passage_number(visit, number, None)
    # The rear camera, not a person, supplied the corrected plate.
    wagon.number_source = "camera"
    wagon.save(update_fields=["number_source"])
    services._log(
        wagon, "automatic_binding",
        f"Вывоз {number}: рейс был открыт под номером {previous} — номер исправлен по памяти тары и выезду",
        None, auto=True, previous_number=previous,
    )
    return wagon


def _recover_entry(entry, number):
    """Open the visit from the unread front weighing that was this truck's entry."""
    wagon = Wagon.objects.create(
        direction=Wagon.PASSAGE, workflow="simple", number=number,
        status=st.ARRIVED, arrived_at=entry.stable_weight_at,
        number_source="camera", number_camera_source=entry.camera,
        cargo_name=settings.VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME,
    )
    entry = services.assign_unassigned_weighing(entry, wagon, None)
    wagon = entry.wagon  # booked: at the silo with its entry weight, not the row created a moment ago
    WeighingIdentityCheck.objects.update_or_create(
        weighing=entry, defaults={"status": "matched", "reason": "automatic_entry", "lease_until": None},
    )
    if entry.capture_id:
        entry.capture.__class__.objects.filter(pk=entry.capture_id).update(wagon_id=wagon.pk, action="entry")
    services._log(
        wagon, "automatic_binding",
        f"Вывоз {number}: заезд восстановлен из неопознанного взвешивания {entry.weight_kg} кг ({_local(entry.stable_weight_at)})",
        None, auto=True, unassigned_id=entry.pk, orientation="front",
        weight_kg=entry.weight_kg, occurred_at=entry.stable_weight_at.isoformat(),
    )
    return wagon


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
            elif verdict == "abandoned":
                # Closed before the new visit is created: one open visit per plate.
                _abandon_visit(wagon, item, number)
                wagon = None
            elif verdict == "unknown":
                # The open visit keeps its own entry; this weighing waits in the
                # review queue until the operator attaches that visit's exit,
                # then books itself as the next visit (see _earlier_entry_pending).
                raise ValueError("previous_exit_missing")
        elif wagon is None:
            _settle_similar_visit(item, number)
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
            entry = _parked_entry(item, number)
            orphan = _orphan_visit(item, number)
            # An unread entry and a visit under a misread plate that both weigh
            # what this truck weighs empty are two stories for one exit: neither
            # is told, the tare comes from history and the operator sees both.
            if orphan is not None and entry is None:
                wagon = _merge_orphan_visit(orphan, number)
            elif entry is not None and orphan is None:
                wagon = _recover_entry(entry, number)
            else:
                wagon = None
            if wagon is not None:
                item = services.assign_unassigned_weighing(item, wagon, None)
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
