from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.cameras import shipping_transport_scheduler as scheduling


@pytest.fixture
def scheduler(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    binding_ids = [1, 2]
    monkeypatch.setattr(scheduling.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(scheduling.ai, "enabled", lambda: True)
    monkeypatch.setattr(
        scheduling.ShippingTransportCamera.objects,
        "values_list",
        lambda *args, **kwargs: binding_ids,
    )
    calls = []

    def submit(worker, binding_id):
        future = Future()
        calls.append((binding_id, future))
        return future

    executor = Mock()
    executor.submit.side_effect = submit
    monkeypatch.setattr(scheduling, "ThreadPoolExecutor", Mock(return_value=executor))
    instance = scheduling.ShippingTransportScheduler(2, max_workers=2)
    yield SimpleNamespace(
        instance=instance, clock=clock, calls=calls, ids=binding_ids, executor=executor
    )
    instance.close()


def test_slow_lane_does_not_block_repeated_due_polls_for_other_lane(scheduler):
    scheduler.instance.tick()
    slow, fast = [future for _, future in scheduler.calls]
    fast.set_result(False)
    scheduler.clock.now = 2
    assert scheduler.instance.tick() == {"processed": 1, "errors": 0}
    scheduler.calls[-1][1].set_result(False)
    scheduler.clock.now = 4
    assert scheduler.instance.tick() == {"processed": 1, "errors": 0}
    assert [binding_id for binding_id, _ in scheduler.calls] == [1, 2, 2, 2]
    assert not slow.done()


def test_same_lane_never_overlaps_or_runs_before_its_due_time(scheduler):
    scheduler.instance.tick()
    for _, future in scheduler.calls:
        future.set_result(False)
    scheduler.clock.now = 1
    assert scheduler.instance.tick()["processed"] == 2
    assert len(scheduler.calls) == 2
    scheduler.clock.now = 2
    scheduler.instance.tick()
    scheduler.clock.now = 50
    scheduler.instance.tick()
    assert [binding_id for binding_id, _ in scheduler.calls] == [1, 2, 1, 2]


def test_capacity_is_bounded_and_waiting_new_lane_gets_next_slot(scheduler):
    scheduler.ids.append(3)
    scheduler.instance.tick()
    assert len(scheduler.calls) == 2
    scheduler.calls[0][1].set_result(False)
    scheduler.clock.now = 2
    scheduler.instance.tick()
    assert [binding_id for binding_id, _ in scheduler.calls] == [1, 2, 3]


def test_removed_binding_is_not_resubmitted_and_new_binding_is_picked_up(scheduler):
    scheduler.instance.tick()
    scheduler.ids[:] = [3]
    for _, future in scheduler.calls:
        future.set_result(False)
    scheduler.clock.now = 2
    scheduler.instance.tick()
    assert [binding_id for binding_id, _ in scheduler.calls] == [1, 2, 3]


def test_disabled_ai_drains_current_results_without_new_requests(
    scheduler, monkeypatch
):
    scheduler.instance.tick()
    for _, future in scheduler.calls:
        future.set_result(True)
    monkeypatch.setattr(scheduling.ai, "enabled", lambda: False)
    scheduler.clock.now = 2
    assert scheduler.instance.tick() == {"processed": 2, "errors": 2}
    assert len(scheduler.calls) == 2


def test_lane_failure_is_reported_and_can_retry(scheduler):
    scheduler.instance.tick()
    scheduler.calls[0][1].set_exception(RuntimeError("lane failed"))
    scheduler.clock.now = 2
    assert scheduler.instance.tick() == {"processed": 1, "errors": 1}
    assert [binding_id for binding_id, _ in scheduler.calls] == [1, 2, 1]


def test_shutdown_drains_workers_and_prevents_more_polls(scheduler):
    scheduler.instance.tick()
    scheduler.instance.close()
    scheduler.executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
    with pytest.raises(RuntimeError, match="closed"):
        scheduler.instance.tick()
