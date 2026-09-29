"""Count-driven shipping sessions projected from the durable event journal.

Counts do not wait for an order, a photo or OCR. Every source event has one
mapping; identity only groups adjacent segments and never changes bag totals.
A segment ends after the idle timeout or, on a wagon conveyor, when the train
changes the wagon (shipping_train_motion), however short the pause.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, prefetch_related_objects
from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.eventlog.services import log_event
from . import ai, shipping_train_motion
from .analytics import (
    add_counts, adjusted_colors, apply_color_targets, apportion, normalized_adjustments,
    shift_shipping_day_colors,
)
from .color_resolution import camera_color_key
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
    policy = ShippingSessionSettings.load()
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
    segment.save(update_fields=["total_bags", "last_counted_at", "ended_at"])
    segment.session.save(update_fields=["total_bags", "last_counted_at", "status", "ended_at"])


def _same_transport(segment, at, changes):
    """Whether a bag at ``at`` still belongs to the segment's transport."""
    if changes is not None and changes.after(segment, at):
        return False
    return at - segment.last_counted_at < timedelta(seconds=segment.idle_timeout_seconds)


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
        first_event=event, first_upstream_event_id=event.upstream_event_id,
    )


@transaction.atomic
def ingest_camera(camera):
    """Replay one page of committed source rows into loading segments."""
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
    ).order_by("upstream_event_id")[:MAX_PAGE_SIZE])
    if not events:
        return result
    already_mapped = set(ShippingLoadingEvent.objects.filter(event_id__in=[e.pk for e in events]).values_list("event_id", flat=True))
    binding = ShippingTransportCamera.objects.filter(conveyor_camera=camera).first()
    segment = ShippingLoadingSegment.objects.select_related("session").filter(camera=camera).order_by("-first_upstream_event_id", "-pk").first()
    changes = shipping_train_motion.load_changes(
        shipping_train_motion.train_camera(binding.recognition_model, binding.number_camera) if binding else None,
        min([event.occurred_at for event in events] + ([segment.last_counted_at] if segment else [])),
    )
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
            continues = segment is not None and configuration_matches and _same_transport(segment, event.occurred_at, changes)
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
    if (
        not upstream.is_caught_up(now=now, max_age=timedelta(seconds=CURSOR_FRESH_SECONDS))
        or projection.last_event_id < upstream.last_event_id
    ):
        return None
    segment = ShippingLoadingSegment.objects.select_related("session").filter(camera=camera, ended_at__isnull=True).first()
    if segment is None:
        return None
    changes = shipping_train_motion.load_changes(
        shipping_train_motion.train_camera(segment.configured_recognition_model, segment.number_camera),
        segment.last_counted_at,
    )
    # Closes after the idle timeout, or as soon as the train took the wagon.
    if _same_transport(segment, upstream.event_caught_up_at, changes):
        return None
    _close_segment(segment)
    return segment.pk


def _same_identity(left, right):
    return (
        left.identity_status == right.identity_status == "identified"
        and bool(left.number) and left.number == right.number
        and left.camera == right.camera and left.recognition_model == right.recognition_model
    )


def _regroup_locked(camera):
    """Recompute contiguous groups after identity, including out-of-order OCR.

    All original segments and photographs remain. Empty aggregate session rows
    redirect to their surviving group so already-issued links remain resolvable,
    and hand their manual colour correction over to it. A split group keeps the
    correction on the original session. The day analytics follows the
    corrections to the day of the session that now shows them.
    """
    segments = list(ShippingLoadingSegment.objects.select_related("session").filter(camera=camera).order_by("first_upstream_event_id", "pk"))
    adjustments = {s.session_id: normalized_adjustments(s.session.colors_adjustment) for s in segments}
    edited = {pk for pk, adjustment in adjustments.items() if adjustment}
    # A correction that moves to a session of another day, or changes what it
    # shows, moves the day analytics with it (read before anything is written).
    corrections_before = _corrections_by_day(ShippingLoadingSession.objects.filter(pk__in=edited)) if edited else {}
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
    moved = {}
    for old_id in old_sessions-used_sessions:
        if adjustments[old_id]:
            moved[owners[old_id]] = add_counts(moved.get(owners[old_id]), adjustments[old_id])
        ShippingLoadingSession.objects.filter(pk=old_id).update(status=ShippingLoadingSession.MERGED, merged_into_id=owners[old_id], total_bags=0, colors_adjustment={})
    for owner_id, delta in moved.items():
        combined = normalized_adjustments(add_counts(adjustments.get(owner_id), delta))
        ShippingLoadingSession.objects.filter(pk=owner_id).update(colors_adjustment=combined)
    if edited:
        corrections_after = _corrections_by_day(ShippingLoadingSession.objects.filter(pk__in=edited | moved.keys()))
        _shift_days(camera, corrections_before, corrections_after)
    return groups


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
        model = next((kind for kind in ("vehicle_number", "wagon_number") if ai.valid_transport_number(number, kind)), "")
    normalized = ai.valid_transport_number(number, model)
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


def _scaled_to_total(colors: dict[str, int], total: int) -> dict[str, int]:
    """Доли — от событий, числа — в масштабе итога отрезка (метод наибольших остатков).

    Обычно событий ровно столько, сколько мешков, и ничего не меняется. После ручной
    поправки итога части должны сходиться с ним, а не с числом событий.
    """
    counted = sum(colors.values())
    if not counted or not total or counted == total:
        return colors
    return {key: value for key, value in apportion(colors, total).items() if value}


@dataclass(frozen=True)
class SessionColors:
    """Bags of one wagon/truck as every screen shows them.

    ``camera`` is the camera's own count by colour (the sum of its segments),
    ``shown`` carries the manual correction on top, and ``segments`` is each
    segment's share of the shown bags, so the segments add up to the session.
    """

    camera: dict[str, int]
    shown: dict[str, int]
    segments: dict[int, int]


def _segment_camera_colors(counts, total_bags):
    """A segment's crossings by colour in the scale of its total."""
    if counts:
        return _scaled_to_total(dict(counts), total_bags)
    # No crossing links: the whole total is bags of an unknown colour.
    return {camera_color_key(None, None): total_bags} if total_bags else {}


def _split_over_segments(shown, parts):
    """Shown bags per segment; ``parts`` are the segments' camera colours in order.

    Each colour follows where the camera counted it; a colour it never saw
    follows the segments' sizes, and with no bags at all goes to the last one.
    """
    totals = dict.fromkeys(parts, 0)
    if not parts:
        return totals
    for color, bags in shown.items():
        weights = {pk: colors.get(color, 0) for pk, colors in parts.items()}
        if not any(weights.values()):
            weights = {pk: sum(colors.values()) for pk, colors in parts.items()}
        if not any(weights.values()):
            weights = {next(reversed(parts)): 1}
        for pk, share in apportion(weights, bags).items():
            totals[pk] += share
    return totals


def session_colors(sessions):
    """``{session_id: SessionColors}`` with one grouped query for all sessions.

    Uses the prefetched ``segments`` when present. Camera colours come from each
    segment's own crossings, so bags the camera could not classify stay visible
    and the parts add up to the total.
    """
    segments = {row.pk: sorted(row.segments.all(), key=lambda part: (part.started_at, part.pk)) for row in sessions}
    counts = defaultdict(Counter)
    rows = (
        ShippingLoadingEvent.objects.filter(segment_id__in=[part.pk for parts in segments.values() for part in parts])
        .values_list("segment_id", "event__color", "event__class_name")
        .annotate(total=Count("pk"))
        .order_by()
    )
    for segment_id, color, class_name, total in rows:
        counts[segment_id][camera_color_key(color, class_name)] += total
    result = {}
    for row in sessions:
        parts = {part.pk: _segment_camera_colors(counts[part.pk], part.total_bags) for part in segments[row.pk]}
        camera = {}
        for colors in parts.values():
            camera = add_counts(camera, colors)
        shown = adjusted_colors(camera, row.colors_adjustment)
        result[row.pk] = SessionColors(camera, shown, _split_over_segments(shown, parts))
    return result


@transaction.atomic
def set_session_colors(session_id, targets):
    """Manual colour correction of one wagon/truck, on top of the camera count.

    Stored as per-colour deltas: the ledger (``total_bags``, segments, event
    links) never changes and bags counted later still add on top. The day
    analytics of the session's day moves by exactly the visible difference.
    """
    hint = ShippingLoadingSession.objects.only("camera").get(pk=session_id)
    # Lock order: camera cursor -> session -> day row (the importer takes the
    # cursor before the day row).
    _lock_camera(hint.camera)
    session = ShippingLoadingSession.objects.select_for_update().get(pk=session_id)
    if session.status == ShippingLoadingSession.MERGED:
        raise NotFound("Сессия объединена с другой. Обновите список сессий")
    colors = session_colors([session])[session.pk]
    session.colors_adjustment, _ = apply_color_targets(colors.camera, session.colors_adjustment, targets)
    session.save(update_fields=["colors_adjustment"])
    # The camera count is the same before and after: the shown colours move
    # exactly as the correction does.
    day = timezone.localdate(session.started_at)
    _shift_days(session.camera, {day: colors.shown}, {day: adjusted_colors(colors.camera, session.colors_adjustment)})
    return session


def _difference(after, before):
    return add_counts(after, {color: -bags for color, bags in before.items()})


def _corrections_by_day(sessions):
    """``{day: {colour: bags}}``: how far corrected sessions show from the camera.

    Summed per day the session list shows them under (the day of the start).
    One grouped query for the segments and one for their crossings.
    """
    sessions = [row for row in sessions if normalized_adjustments(row.colors_adjustment)]
    prefetch_related_objects(sessions, "segments")
    by_session = session_colors(sessions)
    result = {}
    for row in sessions:
        day = timezone.localdate(row.started_at)
        colors = by_session[row.pk]
        result[day] = add_counts(result.get(day), _difference(colors.shown, colors.camera))
    return result


def _shift_days(camera, before, after):
    """Move each day's analytics by how its sessions' visible corrections changed."""
    for day in sorted(before.keys() | after.keys()):
        delta = normalized_adjustments(_difference(after.get(day, {}), before.get(day, {})))
        if delta:
            shift_shipping_day_colors(camera, day, delta)


@transaction.atomic
def book_session_corrections_into_days():
    """One-off for migration 0048: book corrections made before they moved days.

    Until then a wagon/truck correction changed only the session, and its day
    kept the camera count. Books each whole correction into the session's day,
    as an edit does now, so it must run exactly once. Locks as an edit does:
    camera cursor, sessions, then day rows.
    """
    cameras = list(
        ShippingLoadingSession.objects.exclude(status=ShippingLoadingSession.MERGED)
        .exclude(colors_adjustment={}).order_by("camera").values_list("camera", flat=True).distinct()
    )
    for camera in cameras:
        _lock_camera(camera)
        sessions = (
            ShippingLoadingSession.objects.select_for_update().filter(camera=camera)
            .exclude(status=ShippingLoadingSession.MERGED).exclude(colors_adjustment={}).order_by("pk")
        )
        _shift_days(camera, {}, _corrections_by_day(sessions))
