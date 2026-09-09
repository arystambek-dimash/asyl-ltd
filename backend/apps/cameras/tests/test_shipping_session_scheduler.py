from concurrent.futures import Future
from unittest.mock import Mock, patch

from apps.cameras import shipping_session_scheduler as scheduling


class Pool:
    def __init__(self, **kwargs):
        self.jobs = []

    def submit(self, *args):
        future = Future()
        self.jobs.append((future, args))
        return future

    def shutdown(self, **kwargs):
        pass


def test_blocked_ocr_cannot_block_new_count_poll_or_photo_capture():
    with patch.object(scheduling, "ThreadPoolExecutor", Pool), patch.object(
        scheduling.MonoblockCameraSettings, "shipping_sources", return_value=["cam2", "cam3"],
    ), patch.object(scheduling.time, "monotonic", side_effect=[0, 3]):
        scheduler = scheduling.ShippingSessionScheduler(2, max_workers=2)
        scheduler.tick()
        assert len(scheduler._identity.jobs) == 2
        for job, _ in scheduler._counts.jobs:
            job.set_result(False)
        for job, _ in scheduler._photos.jobs:
            job.set_result(True)
        # Both OCR futures remain blocked; healthy imports and first frames
        # nevertheless get new independent slots on the next tick.
        scheduler.tick()
        assert len(scheduler._identity.jobs) == 2
        assert len(scheduler._counts.jobs) == 4
        assert len(scheduler._photos.jobs) == 4
        scheduler.close()


def test_failed_camera_does_not_prevent_projection_of_already_imported_events():
    with patch.object(scheduling.ai, "enabled", return_value=True), patch.object(
        scheduling.event_sync, "sync_camera", side_effect=scheduling.ai.AiUnavailable("offline"),
    ), patch.object(scheduling.event_sync, "mark_sync_failure") as failed, patch.object(
        scheduling.shipping_segments, "ingest_camera",
    ) as ingest, patch.object(scheduling.shipping_segments, "close_idle") as close:
        assert scheduling._sync_and_project("cam2") is True
    failed.assert_called_once()
    assert ingest.call_count == 2
    assert all(call.args == ("cam2",) for call in ingest.call_args_list)
    close.assert_called_once_with("cam2")


def test_projection_runs_before_network_and_import_is_one_page():
    calls = []
    with patch.object(scheduling.ai, "enabled", return_value=True), patch.object(
        scheduling.shipping_segments, "ingest_camera", side_effect=lambda camera: calls.append("project"),
    ), patch.object(scheduling.event_sync, "sync_camera", side_effect=lambda camera, **kwargs: calls.append(kwargs)), patch.object(
        scheduling.shipping_segments, "close_idle", side_effect=lambda camera: calls.append("close"),
    ):
        scheduling._sync_and_project("cam2")
    assert calls == ["project", {"max_pages": 1}, "project", "close"]


def test_new_monitor_uses_count_pipeline_and_preserves_supervisor():
    # Tested through dependency injection by the legacy supervisor suite;
    # ensure the new command does not accidentally start presence-based OCR.
    from apps.cameras.management.commands.monitor_shipping_sessions import Command
    command = Command()
    command.run_monitor = Mock()
    command.handle(once=True, interval=2)
    assert command.run_monitor.call_args.kwargs["scheduler_class"] is scheduling.ShippingSessionScheduler
    assert command.run_monitor.call_args.kwargs["once_callback"] is scheduling.poll_once
