"""Count-driven shipping sessions projected from the durable event journal.

Counts do not wait for an order, a photo or OCR. Every source event has one
mapping; identity only groups adjacent segments and never changes bag totals.
"""
from datetime import timedelta
import re

from django.db import transaction
from django.utils import timezone

from apps.eventlog.services import log_event
from .models import (
    ANALYTICS_SCOPE_SHIPPING, AlwaysOnCounterCursor, AlwaysOnImportedEvent,
    ShippingLoadingCursor, ShippingLoadingEvent, ShippingLoadingSegment,
    ShippingLoadingSession, ShippingSessionSettings, ShippingTransportCamera,
)

CURSOR_FRESH_SECONDS = 30
MAX_PAGE_SIZE = 500


def _lock_camera(camera):
    # Importer -> projection is the sole lock ordering used by ingestion,
    # idle-close and identity writes. Never hold these locks during camera I/O.
    upstream = AlwaysOnCounterCursor.objects.select_for_update().filter(camera=camera).first()
    if upstream is None:
        return None, None, None
    policy, _ = ShippingSessionSettings.objects.get_or_create(singleton=True)
    projection, _ = ShippingLoadingCursor.objects.select_for_update().get_or_create(
        camera=camera, defaults={"activated_at": policy.activated_at}
    )
    return upstream, projection, policy


def _close_segment(segment):
    segment.ended_at = segment.last_counted_at
    segment.save(update_fields=["ended_at"])
    session = segment.session
    session.status, session.ended_at = ShippingLoadingSession.CLOSED, session.last_counted_at
    session.save(update_fields=["status", "ended_at"])


def _flush_segment(segment):
    segment.save(update_fields=["total_bags", "last_event", "last_upstream_event_id", "last_counted_at", "ended_at"])
    segment.session.save(update_fields=["total_bags", "last_counted_at", "status", "ended_at"])


def _new_segment(event, binding, policy):
    model = binding.recognition_model if binding else ""
    session = ShippingLoadingSession.objects.create(
        camera=event.camera, recognition_model=model,
        started_at=event.occurred_at, last_counted_at=event.occurred_at,
    )
    return ShippingLoadingSegment.objects.create(
        session=session, camera=event.camera,
        number_camera=binding.number_camera if binding else "",
        configured_recognition_model=model, recognition_model=model,
        loading_zone=binding.loading_zone if binding else None,
        started_at=event.occurred_at, last_counted_at=event.occurred_at,
        idle_timeout_seconds=policy.idle_timeout_seconds,
        first_event=event, last_event=event,
        first_upstream_event_id=event.upstream_event_id,
        last_upstream_event_id=event.upstream_event_id,
    )


@transaction.atomic
def ingest_camera(camera, *, now=None, limit=MAX_PAGE_SIZE):
    """Replay committed source rows, returning new segment IDs for photo jobs."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("limit must be between 1 and 500")
    upstream, projection, policy = _lock_camera(camera)
    result = {"processed": 0, "ignored": 0, "created_segment_ids": [], "last_event_id": 0}
    if upstream is None or upstream.last_event_id is None:
        return result
    if projection.last_event_id > upstream.last_event_id:
        raise ValueError("shipping_projection_cursor_ahead")
    result["last_event_id"] = projection.last_event_id
    events = list(AlwaysOnImportedEvent.objects.filter(
        camera=camera, upstream_event_id__gt=projection.last_event_id,
        upstream_event_id__lte=upstream.last_event_id,
    ).order_by("upstream_event_id")[:limit])
    if not events:
        return result
    already_mapped = set(ShippingLoadingEvent.objects.filter(event_id__in=[e.pk for e in events]).values_list("event_id", flat=True))
    binding = ShippingTransportCamera.objects.filter(conveyor_camera=camera).first()
    segment = ShippingLoadingSegment.objects.select_related("session").filter(camera=camera).order_by("-first_upstream_event_id", "-pk").first()
    mappings = []
    for event in events:
        if not (event.analytics_scope == ANALYTICS_SCOPE_SHIPPING and event.applied_to_analytics and event.occurred_at >= projection.activated_at):
            result["ignored"] += 1
        elif event.pk not in already_mapped:
            configuration_matches = segment is not None and (
                segment.number_camera == (binding.number_camera if binding else "")
                and segment.configured_recognition_model == (binding.recognition_model if binding else "")
                and segment.loading_zone == (binding.loading_zone if binding else None)
            )
            continues = segment is not None and configuration_matches and (
                event.occurred_at - segment.last_counted_at < timedelta(seconds=segment.idle_timeout_seconds)
            )
            if not continues:
                if segment is not None and segment.ended_at is None:
                    _flush_segment(segment)
                    _close_segment(segment)
                segment = _new_segment(event, binding, policy)
                result["created_segment_ids"].append(segment.pk)
            elif segment.ended_at is not None:
                # A late durable crossing within the original interval reopens
                # it. It never takes a new/current photograph for an old event.
                segment.ended_at = None
            mappings.append(ShippingLoadingEvent(event=event, segment=segment))
            segment.total_bags += 1
            segment.last_event, segment.last_upstream_event_id = event, event.upstream_event_id
            segment.last_counted_at = max(segment.last_counted_at, event.occurred_at)
            session = segment.session
            session.total_bags += 1
            session.last_counted_at = max(session.last_counted_at, event.occurred_at)
            session.status, session.ended_at = ShippingLoadingSession.ACTIVE, None
            result["processed"] += 1
        projection.last_event_id = event.upstream_event_id
    if mappings:
        _flush_segment(segment)
        ShippingLoadingEvent.objects.bulk_create(mappings, batch_size=MAX_PAGE_SIZE)
    projection.save(update_fields=["last_event_id", "updated_at"])
    result["last_event_id"] = projection.last_event_id
    return result


@transaction.atomic
def close_idle(camera, *, now=None):
    """Close only after a fresh, complete, error-free upstream drain."""
    now = now or timezone.now()
    upstream, projection, _ = _lock_camera(camera)
    if upstream is None or upstream.last_event_id is None:
        return None
    caught_up = upstream.event_caught_up_at
    if (
        upstream.event_sync_supported is not True or not upstream.event_boundary_validated
        or upstream.event_sync_error or upstream.event_sync_failed_at
        or upstream.event_drain_required_at or upstream.event_stop_drain_requested_at
        or caught_up is None or caught_up > now
        or now-caught_up > timedelta(seconds=CURSOR_FRESH_SECONDS)
        or projection.last_event_id < upstream.last_event_id
    ):
        return None
    segment = ShippingLoadingSegment.objects.select_related("session").filter(camera=camera, ended_at__isnull=True).first()
    if segment is None or caught_up-segment.last_counted_at < timedelta(seconds=segment.idle_timeout_seconds):
        return None
    _close_segment(segment)
    return segment.pk


def normalized_number(number, recognition_model):
    value = re.sub(r"[\s-]", "", str(number or "").upper())
    if recognition_model == "wagon_number":
        if re.fullmatch(r"[0-9]{8}", value):
            total = sum(sum(int(c) for c in str(int(digit)*(2 if i % 2 == 0 else 1))) for i, digit in enumerate(value[:7]))
            if int(value[-1]) == (10-total % 10) % 10:
                return value
    elif recognition_model == "vehicle_number":
        if value.startswith("KZ"):
            value = value[2:]
        if re.fullmatch(r"(?:[0-9]{3}[A-Z]{2,3}[0-9]{2}|[A-Z][0-9]{3}[A-Z]{3})", value):
            return value
    return ""


def _same_identity(left, right):
    return (
        left.identity_status == right.identity_status == "identified"
        and bool(left.number) and left.number == right.number
        and left.camera == right.camera and left.recognition_model == right.recognition_model
    )


def _regroup_locked(camera):
    """Recompute contiguous groups after identity, including out-of-order OCR.

    All original segments and photographs remain. Empty aggregate session rows
    redirect to their surviving group so already-issued links remain resolvable.
    """
    segments = list(ShippingLoadingSegment.objects.select_related("session").filter(camera=camera).order_by("first_upstream_event_id", "pk"))
    groups, current_order_id = [], None
    for segment in segments:
        if groups and _same_identity(groups[-1][-1], segment):
            if segment.session.order_id is None or current_order_id is None or segment.session.order_id == current_order_id:
                groups[-1].append(segment)
                current_order_id = current_order_id or segment.session.order_id
                continue
        groups.append([segment])
        current_order_id = segment.session.order_id
    used_sessions, changed_segments, old_sessions = set(), [], {s.session_id for s in segments}
    owners = {}
    for group in groups:
        first = group[0]
        session = first.session
        order_ids = {s.session.order_id for s in group if s.session.order_id is not None}
        if session.pk in used_sessions:
            # A corrected identity can split an old group. Do not copy an order
            # onto the newly different transport without explicit linking.
            session = ShippingLoadingSession.objects.create(
                camera=camera, started_at=first.started_at, last_counted_at=first.last_counted_at,
            )
            order_ids = set()
        used_sessions.add(session.pk)
        aggregate_fields = ["camera", "recognition_model", "number", "started_at", "last_counted_at", "total_bags", "status", "ended_at", "merged_into_id", "order_id"]
        before = tuple(getattr(session, name) for name in aggregate_fields)
        session.camera, session.recognition_model = camera, first.recognition_model
        session.number = first.number if first.identity_status == "identified" else ""
        session.started_at = min(s.started_at for s in group)
        session.last_counted_at = max(s.last_counted_at for s in group)
        session.total_bags = sum(s.total_bags for s in group)
        session.status = ShippingLoadingSession.ACTIVE if any(s.ended_at is None for s in group) else ShippingLoadingSession.CLOSED
        session.ended_at = None if session.status == ShippingLoadingSession.ACTIVE else session.last_counted_at
        session.merged_into = None
        if len(order_ids) == 1:
            session.order_id = next(iter(order_ids))
        if before != tuple(getattr(session, name) for name in aggregate_fields):
            session.save(update_fields=["camera", "recognition_model", "number", "started_at", "last_counted_at", "total_bags", "status", "ended_at", "merged_into", "order"])
        for segment in group:
            owners.setdefault(segment.session_id, session.pk)
            if segment.session_id != session.pk:
                segment.session_id = session.pk
                changed_segments.append(segment)
    if changed_segments:
        ShippingLoadingSegment.objects.bulk_update(changed_segments, ["session"])
    for old_id in old_sessions-used_sessions:
        ShippingLoadingSession.objects.filter(pk=old_id).update(status=ShippingLoadingSession.MERGED, merged_into_id=owners[old_id], total_bags=0)
    return groups


@transaction.atomic
def regroup_camera(camera):
    upstream, _, _ = _lock_camera(camera)
    if upstream is not None:
        _regroup_locked(camera)


@transaction.atomic
def apply_identity(segment_id, number, source, user=None, *, expected_lease=None, recognition_model=None):
    hint = ShippingLoadingSegment.objects.only("camera").get(pk=segment_id)
    upstream, _, _ = _lock_camera(hint.camera)
    if upstream is None:
        raise ValueError("shipping_import_cursor_missing")
    segment = ShippingLoadingSegment.objects.select_for_update().get(pk=segment_id)
    if expected_lease is not None and (segment.identity_status != "processing" or segment.identity_lease_until != expected_lease):
        return None
    if source not in {"model", "gpt", "manual"}:
        raise ValueError("invalid_identity_source")
    if source == "manual" and segment.identity_status != "unidentified":
        raise ValueError("shipping_identity_already_resolved")
    model = recognition_model or segment.recognition_model
    if not model and source == "manual":
        # The two validated formats do not overlap: truck plates contain
        # letters; wagon numbers contain eight digits and a valid checksum.
        model = next((kind for kind in ("vehicle_number", "wagon_number") if normalized_number(number, kind)), "")
    normalized = normalized_number(number, model)
    if not normalized:
        raise ValueError("invalid_transport_number")
    previous = {"number": segment.number, "recognition_model": segment.recognition_model, "session_id": segment.session_id}
    segment.number, segment.number_source = normalized, source
    segment.recognition_model, segment.identity_status = model, "identified"
    segment.identity_lease_until, segment.identity_error = None, ""
    segment.save(update_fields=["number", "number_source", "recognition_model", "identity_status", "identity_lease_until", "identity_error"])
    _regroup_locked(segment.camera)
    segment.refresh_from_db()
    log_event("shipping_loading_identified", "Транспорт погрузки распознан", user=user, payload={
        "segment_id": segment.pk, "session_id": segment.session_id, "camera": segment.camera,
        "number": normalized, "recognition_model": model, "source": source, "previous": previous,
    })
    return segment.session
