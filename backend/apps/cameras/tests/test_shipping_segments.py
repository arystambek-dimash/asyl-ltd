from datetime import timedelta
from itertools import permutations

import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.cameras import shipping_segments as segments
from apps.cameras.models import (
    ANALYTICS_SCOPE_SHIPPING, AlwaysOnCounterCursor, AlwaysOnImportedEvent,
    ShippingLoadingCursor, ShippingLoadingEvent, ShippingLoadingSegment,
    ShippingLoadingSession, ShippingSessionSettings, ShippingTransportCamera,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def start():
    return timezone.now()-timedelta(hours=2)


@pytest.fixture(autouse=True)
def policy(start):
    return ShippingSessionSettings.objects.update_or_create(singleton=True, defaults={"activated_at": start, "idle_timeout_seconds": 300})[0]


def add_events(start, seconds, *, camera="cam2", scope=ANALYTICS_SCOPE_SHIPPING, applied=True):
    existing = AlwaysOnImportedEvent.objects.filter(camera=camera).order_by("-upstream_event_id").first()
    previous = existing.upstream_event_id if existing else 0
    rows = AlwaysOnImportedEvent.objects.bulk_create([
        AlwaysOnImportedEvent(camera=camera, upstream_event_id=previous+i+1, occurred_at=start+timedelta(seconds=second), source="sub", mode="always_on", analytics_scope=scope, applied_to_analytics=applied)
        for i, second in enumerate(seconds)
    ])
    AlwaysOnCounterCursor.objects.update_or_create(camera=camera, defaults={
        "last_event_id": rows[-1].upstream_event_id, "last_total": previous+len(rows), "event_compat_total": previous+len(rows),
        "event_sync_supported": True, "event_boundary_validated": True,
        "event_caught_up_at": start+timedelta(seconds=max(seconds)), "event_sync_error": "", "event_sync_failed_at": None,
    })
    return rows


def binding(camera="cam2", number_camera="cam8", model="vehicle_number", zone=None):
    return ShippingTransportCamera.objects.create(conveyor_camera=camera, number_camera=number_camera, recognition_model=model, loading_zone=zone)


def identify(segment, number="123ABC13", model="vehicle_number"):
    return segments.apply_identity(segment.pk, number, "model", recognition_model=model)


def test_first_count_starts_without_order_number_camera_or_ocr(start):
    events = add_events(start, [0, 10, 20])
    result = segments.ingest_camera("cam2")
    segment = ShippingLoadingSegment.objects.get()
    session = segment.session
    assert result["processed"] == 3 and result["created_segment_ids"] == [segment.pk]
    assert segment.identity_status == "pending" and segment.number == "" and not segment.photo
    assert segment.loading_zone is None
    assert segment.first_event_id == events[0].pk and segment.last_event_id == events[-1].pk
    assert segment.total_bags == session.total_bags == 3
    assert session.order_id is None and session.status == "active"
    assert session.started_at == start and session.last_counted_at == start+timedelta(seconds=20)
    assert ShippingLoadingEvent.objects.count() == 3


def test_loading_zone_snapshot_survives_binding_changes_and_splits_segment(start):
    original_zone = [0.1, 0.2, 0.8, 0.9]
    changed_zone = [0.2, 0.1, 0.9, 0.8]
    camera_binding = binding(zone=original_zone)
    add_events(start, [0, 10])
    segments.ingest_camera("cam2")
    original = ShippingLoadingSegment.objects.get()
    assert original.loading_zone == original_zone

    # Equal zone values retain the segment. A different configured crop must
    # capture new evidence without changing the old segment's identity input.
    camera_binding.save(update_fields=["loading_zone"])
    add_events(start, [20])
    assert segments.ingest_camera("cam2")["created_segment_ids"] == []
    camera_binding.loading_zone = changed_zone
    camera_binding.save(update_fields=["loading_zone"])
    add_events(start, [30])
    result = segments.ingest_camera("cam2")
    newer = ShippingLoadingSegment.objects.last()
    original.refresh_from_db()
    assert result["created_segment_ids"] == [newer.pk]
    assert original.pk != newer.pk
    assert original.loading_zone == original_zone
    assert original.total_bags == 3 and original.ended_at == start+timedelta(seconds=20)
    assert newer.loading_zone == changed_zone and newer.total_bags == 1

    camera_binding.loading_zone = None
    camera_binding.save(update_fields=["loading_zone"])
    add_events(start, [40])
    result = segments.ingest_camera("cam2")
    without_zone = ShippingLoadingSegment.objects.last()
    newer.refresh_from_db()
    assert result["created_segment_ids"] == [without_zone.pk]
    assert without_zone.loading_zone is None
    assert newer.loading_zone == changed_zone and newer.ended_at == start+timedelta(seconds=30)
    assert ShippingLoadingEvent.objects.count() == 5


def test_replay_restart_and_cursor_rebuild_do_not_double_count(start):
    add_events(start, [0, 1, 2])
    segments.ingest_camera("cam2")
    assert segments.ingest_camera("cam2")["processed"] == 0
    ShippingLoadingCursor.objects.filter(camera="cam2").update(last_event_id=0)
    assert segments.ingest_camera("cam2")["processed"] == 0
    assert ShippingLoadingSession.objects.get().total_bags == 3
    assert ShippingLoadingEvent.objects.count() == 3


def test_only_shipping_analytics_after_deployment_fence_are_projected(start):
    add_events(start, [-100, -1, 0, 1])
    add_events(start, [2], scope="ai_247")
    add_events(start, [3], applied=False)
    segments.ingest_camera("cam2")
    assert ShippingLoadingSegment.objects.get().total_bags == 2
    assert ShippingLoadingCursor.objects.get().last_event_id == 6
    assert not ShippingLoadingEvent.objects.filter(event__occurred_at__lt=start).exists()


def test_gap_boundary_and_timeout_snapshot(start, policy):
    binding()
    add_events(start, [0, 299, 599, 600])
    segments.ingest_camera("cam2")
    first, second = list(ShippingLoadingSegment.objects.all())
    assert first.total_bags == second.total_bags == 2
    assert first.ended_at == start+timedelta(seconds=299)
    policy.idle_timeout_seconds = 30
    policy.save()
    add_events(start, [660, 1000])
    segments.ingest_camera("cam2")
    second.refresh_from_db()
    assert second.total_bags == 3 and second.idle_timeout_seconds == 300
    assert ShippingLoadingSegment.objects.last().idle_timeout_seconds == 30


@pytest.mark.parametrize("cause", ["old_poll", "failure", "not_caught_up", "projection_behind", "drain_pending", "unsupported", "unvalidated"])
def test_disconnect_or_incomplete_import_never_ends_loading(start, cause):
    add_events(start, [0])
    segments.ingest_camera("cam2")
    cursor = AlwaysOnCounterCursor.objects.get(camera="cam2")
    now = start+timedelta(seconds=400)
    cursor.event_caught_up_at = now
    if cause == "old_poll": cursor.event_caught_up_at = now-timedelta(seconds=31)
    if cause == "failure": cursor.event_sync_error = "upstream unavailable"
    if cause == "not_caught_up": cursor.event_caught_up_at = None
    if cause == "projection_behind": cursor.last_event_id = 2
    if cause == "drain_pending": cursor.event_drain_required_at = now
    if cause == "unsupported": cursor.event_sync_supported = False
    if cause == "unvalidated": cursor.event_boundary_validated = False
    cursor.save()
    assert segments.close_idle("cam2", now=now) is None
    assert ShippingLoadingSegment.objects.get().ended_at is None


def test_fresh_caught_up_poll_closes_at_last_bag_time(start):
    add_events(start, [0, 10])
    segments.ingest_camera("cam2")
    now = start+timedelta(seconds=310)
    AlwaysOnCounterCursor.objects.filter(camera="cam2").update(event_caught_up_at=now)
    assert segments.close_idle("cam2", now=now) == ShippingLoadingSegment.objects.get().pk
    session = ShippingLoadingSession.objects.get()
    assert session.status == "closed" and session.ended_at == start+timedelta(seconds=10)
    assert segments.close_idle("cam2", now=now) is None


def test_late_crossing_within_interval_reopens_without_new_photo(start):
    add_events(start, [0])
    segments.ingest_camera("cam2")
    now = start+timedelta(seconds=400)
    AlwaysOnCounterCursor.objects.filter(camera="cam2").update(event_caught_up_at=now)
    segments.close_idle("cam2", now=now)
    segment = ShippingLoadingSegment.objects.get()
    segment.photo = "saved/original.jpg"
    segment.save()
    add_events(start, [200])
    assert segments.ingest_camera("cam2")["created_segment_ids"] == []
    segment.refresh_from_db()
    assert segment.total_bags == 2 and segment.ended_at is None
    assert segment.photo.name == "saved/original.jpg" and segment.session.status == "active"


@pytest.mark.parametrize("order", list(permutations([0, 1, 2])))
def test_out_of_order_identity_results_merge_only_adjacent_same_number(start, order):
    binding()
    add_events(start, [0, 300, 600])
    segments.ingest_camera("cam2")
    rows = list(ShippingLoadingSegment.objects.all())
    for i, row in enumerate(rows):
        row.photo = f"saved/segment-{i}.jpg"
        row.save()
    for i in order:
        identify(rows[i])
    canonical = ShippingLoadingSession.objects.exclude(status="merged").get()
    assert canonical.total_bags == 3 and canonical.number == "123ABC13"
    assert canonical.status == "active" and canonical.segments.count() == 3
    assert list(canonical.segments.values_list("photo", flat=True)) == ["saved/segment-0.jpg", "saved/segment-1.jpg", "saved/segment-2.jpg"]
    assert ShippingLoadingEvent.objects.count() == 3


@pytest.mark.parametrize("middle", ["unknown", "different", "model"])
def test_unknown_or_different_transport_breaks_global_merging(start, middle):
    binding()
    add_events(start, [0, 300, 600])
    segments.ingest_camera("cam2")
    a, b, c = list(ShippingLoadingSegment.objects.all())
    identify(a)
    identify(c)
    if middle == "different": identify(b, "456DEF13")
    elif middle == "model": identify(b, "12345674", "wagon_number")
    assert ShippingLoadingSession.objects.exclude(status="merged").count() == 3
    assert sum(ShippingLoadingSession.objects.exclude(status="merged").values_list("total_bags", flat=True)) == 3


def test_same_plate_other_camera_does_not_merge(start):
    for camera in ["cam2", "cam3"]:
        add_events(start, [0], camera=camera)
        segments.ingest_camera(camera)
    for segment in ShippingLoadingSegment.objects.all(): identify(segment)
    assert ShippingLoadingSession.objects.count() == 2


def test_stale_identity_worker_cannot_replace_manual_identification(start):
    add_events(start, [0])
    segments.ingest_camera("cam2")
    segment = ShippingLoadingSegment.objects.get()
    lease = timezone.now()+timedelta(minutes=1)
    segment.identity_status, segment.identity_lease_until = "unidentified", None
    segment.save()
    session = segments.apply_identity(segment.pk, "123ABC13", "manual", recognition_model="vehicle_number")
    assert segments.apply_identity(segment.pk, "456DEF13", "gpt", expected_lease=lease, recognition_model="vehicle_number") is None
    session.refresh_from_db()
    assert session.number == "123ABC13"
    with pytest.raises(ValueError, match="already_resolved"):
        segments.apply_identity(segment.pk, "456DEF13", "manual", recognition_model="vehicle_number")


def test_database_prevents_two_active_segments_and_double_event_mapping(start):
    add_events(start, [0])
    segments.ingest_camera("cam2")
    segment = ShippingLoadingSegment.objects.get()
    event = ShippingLoadingEvent.objects.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        ShippingLoadingEvent.objects.create(segment=segment, event_id=event.event_id)
    segment.pk = None
    with pytest.raises(IntegrityError), transaction.atomic():
        segment.save(force_insert=True)


def test_page_processing_queries_do_not_grow_per_bag(start):
    add_events(start, range(250))
    with CaptureQueriesContext(connection) as queries:
        segments.ingest_camera("cam2")
    assert len(queries) < 30
    assert ShippingLoadingSegment.objects.get().total_bags == 250


def test_counts_continue_while_identity_worker_is_slow_or_failed(start):
    add_events(start, [0])
    segments.ingest_camera("cam2")
    part = ShippingLoadingSegment.objects.get()
    part.identity_status = "processing"
    part.identity_lease_until = timezone.now()+timedelta(minutes=2)
    part.save()
    add_events(start, [1, 2, 3])
    segments.ingest_camera("cam2")
    part.refresh_from_db()
    assert part.total_bags == 4 and part.identity_status == "processing"
    part.identity_status, part.identity_error = "unidentified", "recognition_unavailable"
    part.save()
    add_events(start, [4, 5])
    segments.ingest_camera("cam2")
    part.refresh_from_db()
    assert part.total_bags == part.session.total_bags == 6
    assert ShippingLoadingEvent.objects.count() == 6


def test_new_counts_increment_merged_aggregate_without_losing_segments(start):
    add_events(start, [0, 300])
    segments.ingest_camera("cam2")
    first, second = list(ShippingLoadingSegment.objects.all())
    identify(first)
    canonical = identify(second)
    add_events(start, [310, 320])
    segments.ingest_camera("cam2")
    canonical.refresh_from_db()
    first.refresh_from_db()
    second.refresh_from_db()
    assert canonical.total_bags == 4
    assert first.total_bags == 1 and second.total_bags == 3
    assert first.session_id == second.session_id == canonical.pk


def test_manual_identity_can_infer_unconfigured_transport_kind(start):
    add_events(start, [0])
    segments.ingest_camera("cam2")
    part = ShippingLoadingSegment.objects.get()
    part.identity_status = "unidentified"
    part.save()
    session = segments.apply_identity(part.pk, "12345674", "manual")
    assert session.recognition_model == "wagon_number" and session.number == "12345674"


def test_regroup_queries_do_not_grow_per_old_session(start):
    add_events(start, [i*300 for i in range(80)])
    segments.ingest_camera("cam2")
    last = ShippingLoadingSegment.objects.last()
    with CaptureQueriesContext(connection) as queries:
        identify(last)
    assert len(queries) < 20
    assert ShippingLoadingSession.objects.exclude(status="merged").count() == 80
