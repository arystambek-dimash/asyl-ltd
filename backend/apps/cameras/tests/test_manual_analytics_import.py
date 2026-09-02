import hashlib
import json
from datetime import date, datetime
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.cameras.models import (
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    AlwaysOnProductionRun,
    AlwaysOnStockBatch,
    ManualBagAnalyticsImportBatch,
    ManualBagAnalyticsImportEvent,
)

pytestmark = pytest.mark.django_db

UTC = ZoneInfo("UTC")
ALMATY = ZoneInfo("Asia/Almaty")
MODEL_SHA256 = "a" * 64
CAMERA = "cam3"


def _event(
    sequence: int,
    captured_at: datetime,
    *,
    class_name: str,
    color: str,
    brand: str | None,
    origin: str,
) -> dict:
    source_row_id = 100 + sequence
    shadow_run_id = 7 if origin == "shadow_candidate" else None
    origin_key = (
        f"shadow:{shadow_run_id}:{source_row_id}"
        if origin == "shadow_candidate"
        else f"production:{source_row_id}"
    )
    return {
        "idempotency_key": f"bestpt:{MODEL_SHA256}:{origin_key}",
        "captured_at": captured_at.isoformat(),
        "local_date": captured_at.astimezone(ALMATY).date().isoformat(),
        "camera": CAMERA,
        "source": "sub",
        "analytics_scope": "ai_247",
        "mode": "always_on",
        "model_id": "best.pt",
        "model_sha256": MODEL_SHA256,
        "model_event_origin": origin,
        "source_row_id": source_row_id,
        "shadow_run_id": shadow_run_id,
        "frame": 1000 + sequence,
        "track_id": sequence,
        "class_id": sequence - 1,
        "class_name": class_name,
        "confidence": 0.9,
        "direction": "negative",
        "point_x": 320.5,
        "point_y": 240.5,
        "weight_kg": 50.0,
        "color": color,
        "color_confidence": 0.8 if brand is not None else None,
        "brand": brand,
        "brand_confidence": 0.85 if brand is not None else None,
        "sku": f"{color.split('_', 1)[0].lower()}_{brand}" if brand else None,
        "classification_status": "recognized" if brand else "detector_only",
        "sequence": sequence,
    }


def _payload() -> dict:
    events = [
        _event(
            1,
            datetime(2026, 8, 30, 18, 50, tzinfo=UTC),
            class_name="Red_50",
            color="Red_50",
            brand="dikhan_baba",
            origin="shadow_candidate",
        ),
        # The final classifier color intentionally differs from the detector
        # class. Aggregation must use event.color (blue), not class_name (red).
        _event(
            2,
            datetime(2026, 8, 30, 19, 10, tzinfo=UTC),
            class_name="Red_50",
            color="Blue_50",
            brand="korol",
            origin="production",
        ),
        _event(
            3,
            datetime(2026, 8, 30, 19, 20, tzinfo=UTC),
            class_name="Green_50",
            color="Green_50",
            brand=None,
            origin="production",
        ),
    ]
    return {
        "schema": "asyl.best_pt_manual_bag_events.v1",
        "generated_at": "2026-09-01T12:30:00+00:00",
        "model": {"id": "best.pt", "sha256": MODEL_SHA256},
        "coverage": {
            "from_local": events[0]["captured_at"],
            "to_utc": events[-1]["captured_at"],
            "camera": CAMERA,
            "source": "sub",
            "analytics_scope": "ai_247",
            "count_confidence_threshold": 0.4,
            "shadow_candidate_until_utc": events[0]["captured_at"],
            "production_from_utc": events[1]["captured_at"],
        },
        "deduplication": {
            "window_seconds": 1.25,
            "distance_pixels": 48.0,
            "raw_events": 3,
            "duplicates_suppressed": 0,
            "exported_events": 3,
        },
        "summary": {
            "total": 3,
            "by_origin": {"production": 2, "shadow_candidate": 1},
            "by_local_date": {
                "2026-08-30": {"total": 1, "per_class": {"Red_50": 1}},
                "2026-08-31": {
                    "total": 2,
                    "per_class": {"Green_50": 1, "Red_50": 1},
                },
            },
        },
        "events": events,
    }


def _write_payload(tmp_path, payload: dict | None = None):
    raw = json.dumps(payload or _payload(), sort_keys=True).encode()
    path = tmp_path / "manual-events.json"
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def _call(path, sha256, *, apply=False):
    output = StringIO()
    call_command(
        "import_manual_bag_analytics",
        str(path),
        expected_sha256=sha256,
        apply=apply,
        stdout=output,
    )
    return json.loads(output.getvalue())


@pytest.fixture(autouse=True)
def repaired_production_anchor():
    """Model the prerequisite cursor repair used by the production backfill."""

    production = [
        event
        for event in _payload()["events"]
        if event["model_event_origin"] == "production"
    ]
    cursor = AlwaysOnCounterCursor.objects.create(
        camera=CAMERA,
        last_total=500,
        last_event_id=max(event["source_row_id"] for event in production),
        event_compat_total=500,
        event_sync_supported=True,
    )
    anchors = []
    for offset, event in enumerate(production, start=1):
        anchors.append(
            AlwaysOnImportedEvent.objects.create(
                camera=CAMERA,
                upstream_event_id=event["source_row_id"],
                occurred_at=datetime.fromisoformat(event["captured_at"]),
                source=event["source"],
                mode=event["mode"],
                class_name=event["class_name"],
                color=event["color"],
                color_confidence=event["color_confidence"],
                brand=event["brand"],
                brand_confidence=event["brand_confidence"],
                sku=event["sku"],
                classification_status=event["classification_status"],
                total_after=500 + offset,
                applied_to_analytics=False,
            )
        )
    return cursor, anchors


def test_dry_run_validates_and_writes_nothing(tmp_path):
    path, sha256 = _write_payload(tmp_path)

    result = _call(path, sha256)

    assert result["mode"] == "dry-run"
    assert result["status"] == "would_import"
    assert result["event_count"] == 3
    assert result["per_day"] == {
        "2026-08-30": {
            "total": 1,
            "per_color": {"red": 1},
            "per_brand": {"dikhan_baba": 1},
        },
        "2026-08-31": {
            "total": 2,
            "per_color": {"blue": 1, "green": 1},
            "per_brand": {"korol": 1, "unclassified": 1},
        },
    }
    assert not ManualBagAnalyticsImportBatch.objects.exists()
    assert not ManualBagAnalyticsImportEvent.objects.exists()
    assert not AlwaysOnDailyAnalytics.objects.exists()


def test_apply_adds_exact_colors_and_brands_to_unarchived_rows(tmp_path):
    existing = AlwaysOnDailyAnalytics.objects.create(
        camera=CAMERA,
        day=date(2026, 8, 31),
        model_total=4,
        model_per_color={"red": 4},
        model_per_brand={"unknown": 4},
        adjustment=2,
    )
    path, sha256 = _write_payload(tmp_path)

    result = _call(path, sha256, apply=True)

    assert result["status"] == "imported"
    aug30 = AlwaysOnDailyAnalytics.objects.get(camera=CAMERA, day="2026-08-30")
    assert aug30.model_total == 1
    assert aug30.model_per_color == {"red": 1}
    assert aug30.model_per_brand == {"dikhan_baba": 1}
    existing.refresh_from_db()
    assert existing.model_total == 6
    assert existing.model_per_color == {"red": 4, "blue": 1, "green": 1}
    assert existing.model_per_brand == {
        "unknown": 4,
        "korol": 1,
        "unclassified": 1,
    }
    assert existing.adjustment == 2
    batch = ManualBagAnalyticsImportBatch.objects.get()
    assert batch.file_sha256 == sha256
    assert batch.event_count == 3
    assert batch.events.count() == 3
    second = batch.events.get(sequence=2)
    assert second.class_name == "Red_50"
    assert second.color == "Blue_50"
    assert second.brand == "korol"


def test_apply_replay_is_an_idempotent_noop(tmp_path):
    path, sha256 = _write_payload(tmp_path)
    first = _call(path, sha256, apply=True)
    rows_before = list(
        AlwaysOnDailyAnalytics.objects.order_by("day").values(
            "day", "model_total", "model_per_color", "model_per_brand"
        )
    )

    second = _call(path, sha256, apply=True)

    assert second["status"] == "already_imported"
    assert second["batch_id"] == first["batch_id"]
    assert ManualBagAnalyticsImportBatch.objects.count() == 1
    assert ManualBagAnalyticsImportEvent.objects.count() == 3
    assert (
        list(
            AlwaysOnDailyAnalytics.objects.order_by("day").values(
                "day", "model_total", "model_per_color", "model_per_brand"
            )
        )
        == rows_before
    )


def test_replay_fails_closed_if_audit_ledger_changed(tmp_path):
    path, sha256 = _write_payload(tmp_path)
    _call(path, sha256, apply=True)
    ManualBagAnalyticsImportEvent.objects.filter(sequence=2).update(color="Red_50")
    rows_before = list(AlwaysOnDailyAnalytics.objects.order_by("day").values())

    with pytest.raises(CommandError, match="ledger has changed"):
        _call(path, sha256, apply=True)

    assert list(AlwaysOnDailyAnalytics.objects.order_by("day").values()) == rows_before


@pytest.mark.parametrize("mutation", ["schema", "duplicate"])
def test_rejects_changed_schema_and_duplicate_keys(tmp_path, mutation):
    payload = _payload()
    if mutation == "schema":
        payload["schema"] = "asyl.best_pt_manual_bag_events.v2"
    else:
        payload["events"][1]["source_row_id"] = payload["events"][0]["source_row_id"]
        payload["events"][1]["shadow_run_id"] = payload["events"][0]["shadow_run_id"]
        payload["events"][1]["model_event_origin"] = "shadow_candidate"
        payload["events"][1]["idempotency_key"] = payload["events"][0][
            "idempotency_key"
        ]
        payload["summary"]["by_origin"] = {
            "production": 1,
            "shadow_candidate": 2,
        }
    path, sha256 = _write_payload(tmp_path, payload)

    with pytest.raises(CommandError):
        _call(path, sha256)

    assert not ManualBagAnalyticsImportBatch.objects.exists()


def test_rejects_checksum_mismatch_before_json_import(tmp_path):
    path, _ = _write_payload(tmp_path)

    with pytest.raises(CommandError, match="SHA256 mismatch"):
        _call(path, "b" * 64)

    assert not ManualBagAnalyticsImportBatch.objects.exists()


def test_rejects_overlapping_shadow_and_production_transition(tmp_path):
    payload = _payload()
    payload["coverage"]["production_from_utc"] = payload["coverage"][
        "shadow_candidate_until_utc"
    ]
    path, sha256 = _write_payload(tmp_path, payload)

    with pytest.raises(CommandError, match="transition"):
        _call(path, sha256)

    assert not ManualBagAnalyticsImportBatch.objects.exists()


def test_archived_target_row_aborts_the_whole_import(tmp_path):
    AlwaysOnDailyAnalytics.objects.create(
        camera=CAMERA,
        day=date(2026, 8, 31),
        model_total=4,
        model_per_color={"red": 4},
        model_per_brand={"unknown": 4},
        archived_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    path, sha256 = _write_payload(tmp_path)

    with pytest.raises(CommandError, match="already archived"):
        _call(path, sha256, apply=True)

    assert not ManualBagAnalyticsImportBatch.objects.exists()
    assert not ManualBagAnalyticsImportEvent.objects.exists()
    assert AlwaysOnDailyAnalytics.objects.count() == 1


def test_apply_does_not_touch_cursor_native_events_runs_or_stock(tmp_path):
    cursor = AlwaysOnCounterCursor.objects.get(camera=CAMERA)
    native = AlwaysOnImportedEvent.objects.create(
        camera=CAMERA,
        upstream_event_id=12,
        occurred_at=datetime(2026, 8, 29, tzinfo=UTC),
        source="sub",
        mode="always_on",
        class_name="Red_50",
        total_after=12,
        applied_to_analytics=True,
    )
    run = AlwaysOnProductionRun.objects.create(
        camera=CAMERA,
        business_day=date(2026, 8, 29),
        color="red",
        started_at=datetime(2026, 8, 29, tzinfo=UTC),
        last_counted_at=datetime(2026, 8, 29, 1, tzinfo=UTC),
        ended_at=datetime(2026, 8, 29, 1, tzinfo=UTC),
        model_bags=12,
    )
    batch = AlwaysOnStockBatch.objects.create(
        camera=CAMERA,
        business_day=date(2026, 8, 29),
        scheduled_for=datetime(2026, 8, 30, tzinfo=UTC),
        total_bags=12,
    )
    cursor_before = AlwaysOnCounterCursor.objects.values().get(pk=cursor.pk)
    native_before = AlwaysOnImportedEvent.objects.values().get(pk=native.pk)
    run_before = AlwaysOnProductionRun.objects.values().get(pk=run.pk)
    stock_before = AlwaysOnStockBatch.objects.values().get(pk=batch.pk)
    path, sha256 = _write_payload(tmp_path)

    _call(path, sha256, apply=True)

    assert AlwaysOnCounterCursor.objects.values().get(pk=cursor.pk) == cursor_before
    assert AlwaysOnImportedEvent.objects.values().get(pk=native.pk) == native_before
    assert AlwaysOnProductionRun.objects.values().get(pk=run.pk) == run_before
    assert AlwaysOnStockBatch.objects.values().get(pk=batch.pk) == stock_before


@pytest.mark.parametrize("broken_state", ["missing_cursor", "missing_anchor", "applied"])
def test_new_import_requires_completed_cursor_repair(tmp_path, broken_state):
    if broken_state == "missing_cursor":
        AlwaysOnCounterCursor.objects.all().delete()
    elif broken_state == "missing_anchor":
        AlwaysOnImportedEvent.objects.order_by("upstream_event_id").last().delete()
    else:
        AlwaysOnImportedEvent.objects.order_by("upstream_event_id").update(
            applied_to_analytics=True
        )
    path, sha256 = _write_payload(tmp_path)

    with pytest.raises(CommandError, match="cursor repair|anchor"):
        _call(path, sha256, apply=True)

    assert not ManualBagAnalyticsImportBatch.objects.exists()
    assert not ManualBagAnalyticsImportEvent.objects.exists()
    assert not AlwaysOnDailyAnalytics.objects.exists()


def test_new_import_rejects_changed_production_anchor(tmp_path):
    changed = AlwaysOnImportedEvent.objects.order_by("upstream_event_id").last()
    assert changed is not None
    changed.color = "Red_50"
    changed.save(update_fields=["color"])
    path, sha256 = _write_payload(tmp_path)

    with pytest.raises(CommandError, match="changed contents"):
        _call(path, sha256, apply=True)

    assert not ManualBagAnalyticsImportBatch.objects.exists()


def test_manual_import_audit_instances_are_immutable(tmp_path):
    path, sha256 = _write_payload(tmp_path)
    _call(path, sha256, apply=True)
    batch = ManualBagAnalyticsImportBatch.objects.get()
    event = batch.events.first()

    batch.source_filename = "changed.json"
    with pytest.raises(ValueError, match="immutable"):
        batch.save()
    with pytest.raises(ValueError, match="cannot be deleted"):
        batch.delete()
    assert event is not None
    event.color = "changed"
    with pytest.raises(ValueError, match="immutable"):
        event.save()
    with pytest.raises(ValueError, match="cannot be deleted"):
        event.delete()
