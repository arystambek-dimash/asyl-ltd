import json
from datetime import date, datetime
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.cameras import production, production_repair
from apps.cameras.models import (
    AlwaysOnCounterCursor,
    AlwaysOnImportedEvent,
    AlwaysOnProductionCorrection,
    AlwaysOnProductionRun,
)

pytestmark = pytest.mark.django_db

ALMATY = ZoneInfo("Asia/Almaty")
CAMERA = "cam3"
LOCAL_DAY = date(2026, 8, 25)


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 25, hour, minute, tzinfo=ALMATY)


def _event(
    event_id: int,
    occurred_at: datetime,
    color: str,
    *,
    applied: bool = True,
    classified_color: str | None = None,
) -> AlwaysOnImportedEvent:
    return AlwaysOnImportedEvent.objects.create(
        camera=CAMERA,
        upstream_event_id=event_id,
        occurred_at=occurred_at,
        source="sub",
        mode="always_on",
        class_name=f"{color}_bag",
        color=classified_color,
        total_after=event_id,
        applied_to_analytics=applied,
    )


def test_repair_prefers_classified_color_and_keeps_legacy_fallback():
    classified = _event(
        1,
        _at(13, 36),
        "red",
        classified_color="Blue_50",
    )
    legacy = _event(2, _at(13, 37), "green")

    assert production_repair._event_color(classified) == "blue"
    assert production_repair._event_color(legacy) == "green"


def _cursor(*, event_count: int, last_event_id: int | None = None):
    return AlwaysOnCounterCursor.objects.create(
        camera=CAMERA,
        last_total=event_count,
        last_per_color={},
        last_event_id=last_event_id or event_count,
        event_compat_total=event_count,
        event_sync_supported=True,
        event_boundary_validated=True,
    )


def _run(
    color: str,
    started_at: datetime,
    last_counted_at: datetime,
    bags: int,
    *,
    is_open: bool = False,
    business_day: date | None = None,
) -> AlwaysOnProductionRun:
    return AlwaysOnProductionRun.objects.create(
        camera=CAMERA,
        business_day=business_day or production.business_day_for(started_at),
        color=color,
        started_at=started_at,
        last_counted_at=last_counted_at,
        ended_at=None if is_open else last_counted_at,
        model_bags=bags,
    )


@pytest.fixture
def overlapping_color_runs():
    # This reproduces the old per-colour-open-run bug: green remains active
    # while the later blue period is already active.
    event_specs = [
        (1, _at(13, 36), "red"),
        (2, _at(13, 37), "red"),
        (3, _at(13, 38), "green"),
        (4, _at(13, 39), "green"),
        (5, _at(13, 40), "blue"),
        (6, _at(13, 41), "blue"),
        (7, _at(13, 42), "red"),
    ]
    for event_id, occurred_at, color in event_specs:
        _event(event_id, occurred_at, color)
    _cursor(event_count=len(event_specs))

    # Legacy logic reopens the first red row after blue instead of creating a
    # fourth A→B→C→A period, while green and blue remain open concurrently.
    _run("red", _at(13, 36), _at(13, 42), 3, is_open=True)
    _run("green", _at(13, 38), _at(13, 39), 2, is_open=True)
    _run("blue", _at(13, 40), _at(13, 41), 2, is_open=True)


def _run_snapshot():
    return list(
        AlwaysOnProductionRun.objects.order_by("started_at", "id").values(
            "id",
            "business_day",
            "color",
            "started_at",
            "last_counted_at",
            "ended_at",
            "model_bags",
            "is_approximate",
        )
    )


def test_command_is_dry_run_by_default(overlapping_color_runs):
    before = _run_snapshot()
    output = StringIO()

    call_command(
        "rebuild_event_production_runs",
        camera=CAMERA,
        day=LOCAL_DAY.isoformat(),
        stdout=output,
    )

    assert _run_snapshot() == before
    payload = json.loads(output.getvalue())
    assert payload["mode"] == "dry-run"
    assert payload["status"] == "would_change"
    assert payload["event_count"] == 7
    assert payload["per_color"] == {"blue": 2, "green": 2, "red": 3}


def test_apply_repairs_overlap_preserves_ledgers_and_is_idempotent(
    overlapping_color_runs,
):
    correction = AlwaysOnProductionCorrection.objects.create(
        camera=CAMERA,
        business_day=LOCAL_DAY,
        color="red",
        delta=1,
        reason="existing audited correction",
    )
    imported_before = list(
        AlwaysOnImportedEvent.objects.order_by("upstream_event_id").values_list(
            "id", "upstream_event_id", "class_name", "applied_to_analytics"
        )
    )
    cursor_before = AlwaysOnCounterCursor.objects.values().get(camera=CAMERA)
    output = StringIO()

    call_command(
        "rebuild_event_production_runs",
        camera=CAMERA,
        day=LOCAL_DAY.isoformat(),
        apply=True,
        stdout=output,
    )

    rows = list(AlwaysOnProductionRun.objects.order_by("started_at", "id"))
    assert [row.color for row in rows] == ["red", "green", "blue", "red"]
    assert [row.model_bags for row in rows] == [2, 2, 2, 1]
    assert [row.ended_at for row in rows] == [
        _at(13, 37),
        _at(13, 39),
        _at(13, 41),
        _at(13, 42),
    ]
    assert AlwaysOnProductionRun.objects.filter(ended_at__isnull=True).count() == 0
    assert json.loads(output.getvalue())["status"] == "changed"

    assert list(
        AlwaysOnImportedEvent.objects.order_by("upstream_event_id").values_list(
            "id", "upstream_event_id", "class_name", "applied_to_analytics"
        )
    ) == imported_before
    assert AlwaysOnCounterCursor.objects.values().get(camera=CAMERA) == cursor_before
    assert AlwaysOnProductionCorrection.objects.filter(pk=correction.pk).exists()

    repaired_ids = list(
        AlwaysOnProductionRun.objects.order_by("started_at", "id").values_list(
            "id", flat=True
        )
    )
    second_output = StringIO()
    call_command(
        "rebuild_event_production_runs",
        camera=CAMERA,
        day=LOCAL_DAY.isoformat(),
        apply=True,
        stdout=second_output,
    )
    assert list(
        AlwaysOnProductionRun.objects.order_by("started_at", "id").values_list(
            "id", flat=True
        )
    ) == repaired_ids
    assert json.loads(second_output.getvalue())["status"] == "unchanged"


def test_apply_aborts_without_writes_when_candidate_balance_mismatches(
    overlapping_color_runs,
):
    # Preserve the overall count but move one bag between colours.  A total-
    # only check would miss this and corrupt the colour/stock ledger.
    AlwaysOnProductionRun.objects.filter(color="green").update(model_bags=1)
    AlwaysOnProductionRun.objects.filter(color="blue").update(model_bags=3)
    before = _run_snapshot()

    with pytest.raises(CommandError, match="per-color totals"):
        call_command(
            "rebuild_event_production_runs",
            camera=CAMERA,
            day=LOCAL_DAY.isoformat(),
            apply=True,
        )

    assert _run_snapshot() == before


def test_apply_aborts_if_business_day_color_bucket_would_change():
    _event(1, _at(18, 59), "red")
    _event(2, _at(19, 0), "blue")
    _cursor(event_count=2)

    # Overall and per-colour totals match, but the colours are assigned to the
    # opposite sides of the 19:00 stock cutoff.
    _run("blue", _at(18, 59), _at(18, 59), 1)
    _run("red", _at(19, 0), _at(19, 0), 1)
    before = _run_snapshot()

    with pytest.raises(CommandError, match="business-day/color totals"):
        call_command(
            "rebuild_event_production_runs",
            camera=CAMERA,
            day=LOCAL_DAY.isoformat(),
            apply=True,
        )

    assert _run_snapshot() == before


def test_apply_aborts_on_unapplied_event_before_covered_interval():
    _event(1, _at(13, 35), "red", applied=False)
    _event(2, _at(13, 36), "red")
    _event(3, _at(13, 38), "red")
    _cursor(event_count=2, last_event_id=3)
    _run("red", _at(13, 36), _at(13, 38), 2)
    before = _run_snapshot()

    with pytest.raises(CommandError, match="unapplied continuous-analytics event"):
        call_command(
            "rebuild_event_production_runs",
            camera=CAMERA,
            day=LOCAL_DAY.isoformat(),
            apply=True,
        )

    assert _run_snapshot() == before


def test_rebuild_splits_gap_and_every_color_transition_in_event_order():
    event_specs = [
        (1, _at(13, 36), "red"),
        # Exactly five minutes is still one contiguous period.
        (2, _at(13, 41), "red"),
        # Six minutes starts another period even though the colour is equal.
        (3, _at(13, 47), "red"),
        # Same timestamp is ordered by upstream id and closes red immediately.
        (4, _at(13, 47), "blue"),
        # Returning to red never reopens either earlier red period.
        (5, _at(13, 48), "red"),
    ]
    for event_id, occurred_at, color in event_specs:
        _event(event_id, occurred_at, color)
    _cursor(event_count=5)
    _run("red", _at(13, 36), _at(13, 48), 4)
    _run("blue", _at(13, 47), _at(13, 47), 1)

    call_command(
        "rebuild_event_production_runs",
        camera=CAMERA,
        day=LOCAL_DAY.isoformat(),
        apply=True,
    )

    rows = list(AlwaysOnProductionRun.objects.order_by("started_at", "id"))
    assert [row.color for row in rows] == ["red", "red", "blue", "red"]
    assert [row.model_bags for row in rows] == [2, 1, 1, 1]
    assert [(row.started_at, row.last_counted_at) for row in rows] == [
        (_at(13, 36), _at(13, 41)),
        (_at(13, 47), _at(13, 47)),
        (_at(13, 47), _at(13, 47)),
        (_at(13, 48), _at(13, 48)),
    ]
    assert all(row.ended_at == row.last_counted_at for row in rows)


def test_command_rejects_current_or_future_local_day(monkeypatch):
    monkeypatch.setattr(
        "apps.cameras.production_repair.timezone.now",
        lambda: datetime(2026, 8, 26, 12, 0, tzinfo=ALMATY),
    )

    with pytest.raises(CommandError, match="strictly before"):
        call_command(
            "rebuild_event_production_runs",
            camera=CAMERA,
            day="2026-08-26",
            apply=True,
        )

    assert not AlwaysOnProductionRun.objects.exists()


def test_apply_aborts_on_production_tail_after_last_imported_event():
    _event(1, _at(13, 36), "red")
    _event(2, _at(13, 37), "red")
    _cursor(event_count=2)
    _run("red", _at(13, 36), _at(13, 37), 2)
    _run("blue", _at(14, 0), _at(14, 1), 2)
    before = _run_snapshot()

    with pytest.raises(CommandError, match="after exact event coverage"):
        call_command(
            "rebuild_event_production_runs",
            camera=CAMERA,
            day=LOCAL_DAY.isoformat(),
            apply=True,
        )

    assert _run_snapshot() == before
