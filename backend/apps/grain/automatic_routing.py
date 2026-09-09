"""Book saved scale events by an explicitly read plate and direction.

The model supplies identity only. Weights, timestamps and tare references always
come from committed physical measurements; every write shares the lane mutex.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import historical_tare, services, statuses as st
from .models import PassageScaleAutomationState, UnassignedWeighing, Wagon


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
        if check and check.status in {"review", "matched"}:
            continue
        if check and check.reason == "daily_budget_exhausted":
            continue
        if not entry.photo and photo_delivery_status(entry) == "unavailable":
            continue
        # Processing is bounded by photo retry limits and GPT attempt limits.
        # Terminal unreadable entries do not block unrelated traffic forever.
        return True
    return False


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
    open_visits = list(Wagon.objects.select_for_update().filter(
        direction=Wagon.PASSAGE, number=number, status__in=st.ON_SITE_STATUSES,
    ).order_by("pk")[:2])
    if len(open_visits) > 1:
        raise ValueError("ambiguous_active_passage")
    wagon = open_visits[0] if open_visits else None
    if orientation == "rear" and _earlier_entry_pending(item, number):
        raise ValueError("earlier_entry_pending")
    if orientation == "front":
        if wagon is None:
            # Direct creation intentionally avoids the manual-action lane fence.
            wagon = Wagon.objects.create(
                direction=Wagon.PASSAGE, workflow="simple", number=number,
                status=st.ARRIVED, arrived_at=item.stable_weight_at,
                number_source="camera", number_camera_source=item.camera,
                cargo_name=settings.VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME,
            )
        if wagon.status == st.AT_SILO and wagon.tare_weight_kg is None:
            entry_at = services._passage_entry_at(wagon)
            if entry_at is None or item.stable_weight_at <= entry_at:
                raise ValueError("passage_time_conflict")
            # A fresh, distinct front weighing updates the tare for this plate.
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
        source = historical_tare.latest_before(item, number)
        if source is None:
            raise ValueError("saved_tare_missing")
        if item.weight_kg <= source.weight_kg:
            raise ValueError("exit_weight_not_greater")
        item = historical_tare.complete(
            item, None, reference_record=source.pk, number=number,
            reason="Автоматический выезд: последняя измеренная тара по госномеру", automatic=True,
        )
    if item.capture_id:
        item.capture.__class__.objects.filter(pk=item.capture_id).update(wagon_id=item.wagon_id, action=item.action)
    services._log(
        item.wagon, "automatic_binding", f"Вывоз {number}: автоматический {'заезд' if item.action == 'entry' else 'выезд'}",
        None, auto=True, unassigned_id=item.pk, orientation=orientation,
        weight_kg=item.weight_kg, occurred_at=item.stable_weight_at.isoformat(),
    )
    return item
