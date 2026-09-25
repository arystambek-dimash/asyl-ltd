from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.cameras import ai
from apps.cameras import shipping_segments as segments
from apps.cameras import shipping_train_motion as train_motion
from apps.cameras.models import (
    AlwaysOnCounterCursor, ShippingLoadingSegment, ShippingLoadingSession, ShippingSessionSettings,
    ShippingTrainMove, ShippingTransportCamera,
)
from apps.cameras.tests.shipping_fakes import add_events

pytestmark = pytest.mark.django_db


@pytest.fixture
def start():
    return timezone.now()-timedelta(hours=2)


@pytest.fixture(autouse=True)
def policy(start):
    return ShippingSessionSettings.objects.update_or_create(singleton=True, defaults={"activated_at": start, "idle_timeout_seconds": 300})[0]


@pytest.fixture
def wagons():
    return ShippingTransportCamera.objects.create(conveyor_camera="cam2", number_camera="cam12", recognition_model="wagon_number")


@pytest.fixture(autouse=True)
def quiet_warnings():
    train_motion._last_warning.clear()


def at(start, second):
    return start+timedelta(seconds=second)


def move(start, began, stopped, *, high=None, camera="cam12"):
    return ShippingTrainMove.objects.create(
        camera=camera, started_at=at(start, began), stopped_at=at(start, stopped),
        seconds=stopped-began, high_seconds=stopped-began if high is None else high, mean_fraction=0.8,
    )


def bags():
    return [segment.total_bags for segment in ShippingLoadingSegment.objects.order_by("started_at")]


def test_wagon_change_splits_loading_that_never_paused_for_the_idle_timeout(start, wagons):
    # Prod 19.09: the next wagon started 4.7 min after the last bag of the
    # previous one, and both became one 2426-bag session.
    move(start, 100, 127)
    add_events(start, [0, 60, 95, 150, 200])
    segments.ingest_camera("cam2")
    assert bags() == [3, 2]
    first, second = ShippingLoadingSegment.objects.order_by("started_at")
    assert first.ended_at == at(start, 95) and second.started_at == at(start, 150)


@pytest.mark.parametrize("high", [3.5, 6.5, 11.9])
def test_jolts_people_and_door_alignment_do_not_change_the_wagon(start, wagons, high):
    move(start, 100, 140, high=high)
    add_events(start, [0, 95, 200])
    segments.ingest_camera("cam2")
    assert bags() == [3]


def test_long_pause_still_splits_and_identity_joins_the_same_wagon(start, wagons):
    add_events(start, [0, 1500])
    segments.ingest_camera("cam2")
    assert bags() == [1, 1]
    for segment in ShippingLoadingSegment.objects.all():
        segments.apply_identity(segment.pk, "28060325", "model")
    assert ShippingLoadingSession.objects.exclude(status="merged").get().total_bags == 2


def test_wagon_still_creeping_in_at_its_first_bag_is_not_split_again(start, wagons):
    move(start, 100, 127)  # the loaded wagon taken away
    move(start, 200, 220)  # the next one pushed in, first bag while it moves
    add_events(start, [0, 10, 203, 221, 222])
    segments.ingest_camera("cam2")
    assert bags() == [2, 3]


def test_truck_conveyor_ignores_train_movements(start):
    ShippingTransportCamera.objects.create(conveyor_camera="cam2", number_camera="cam12", recognition_model="vehicle_number")
    move(start, 100, 127)
    add_events(start, [0, 150])
    segments.ingest_camera("cam2")
    assert bags() == [2]


def close(start, second):
    AlwaysOnCounterCursor.objects.filter(camera="cam2").update(event_caught_up_at=at(start, second))
    return segments.close_idle("cam2", now=at(start, second))


def test_wagon_session_closes_as_soon_as_the_train_takes_the_wagon(start, wagons):
    add_events(start, [0, 10])
    segments.ingest_camera("cam2")
    assert close(start, 100) is None
    move(start, 110, 140)
    assert close(start, 145) == ShippingLoadingSegment.objects.get().pk
    assert ShippingLoadingSegment.objects.get().ended_at == at(start, 10)


def test_idle_timeout_still_closes_a_watched_wagon(start, wagons):
    add_events(start, [0, 10])
    segments.ingest_camera("cam2")
    assert close(start, 400) == ShippingLoadingSegment.objects.get().pk


def status(start, moves):
    return {"state": "still", "status": "online", "moves": [
        {"id": index, "started_at": at(start, began).isoformat(), "stopped_at": at(start, stopped).isoformat(),
         "seconds": stopped-began, "high_seconds": stopped-began-1, "mean_fraction": 0.8, "interrupted": False}
        for index, (began, stopped) in enumerate(moves, 1)
    ]}


def test_poll_records_each_movement_once(start, wagons):
    with patch.object(ai, "arch_motion", return_value=status(start, [(100, 127), (200, 220)])) as probe:
        assert train_motion.poll("cam2") == 2
        assert train_motion.poll("cam2") == 2
    probe.assert_called_with("cam12")
    assert list(ShippingTrainMove.objects.values_list("stopped_at", "high_seconds")) == [(at(start, 127), 26), (at(start, 220), 19)]


def test_poll_skips_a_malformed_movement_and_keeps_the_rest(start, wagons):
    payload = status(start, [(100, 127)])
    payload["moves"].append({"started_at": "bad", "stopped_at": None, "seconds": 1})
    with patch.object(ai, "arch_motion", return_value=payload), patch.object(train_motion.log, "warning") as warning:
        assert train_motion.poll("cam2") == 1
        assert train_motion.poll("cam2") == 1
    assert ShippingTrainMove.objects.count() == 1
    warning.assert_called_once()


@pytest.mark.parametrize("answer", [
    ai.AiUnavailable("offline"),
    ai.AiError(404, "arch motion is not configured for this camera"),
    {"state": "still"},  # camera PC before the update
    {"state": "unknown", "status": "zone_missing_or_disabled", "moves": []},
    {"state": "unknown", "status": "zone_source_mismatch", "moves": []},
])
def test_unusable_monitor_answers_record_nothing_and_warn_rarely(wagons, answer):
    kwargs = {"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer}
    with patch.object(ai, "arch_motion", **kwargs), patch.object(train_motion.log, "warning") as warning:
        assert train_motion.poll("cam2") == 0
        assert train_motion.poll("cam2") == 0
    warning.assert_called_once()
    assert not ShippingTrainMove.objects.exists()


def test_truck_conveyor_is_not_polled():
    ShippingTransportCamera.objects.create(conveyor_camera="cam5", number_camera="cam6", recognition_model="vehicle_number")
    with patch.object(ai, "arch_motion") as probe:
        assert train_motion.poll("cam5") == 0
    probe.assert_not_called()
