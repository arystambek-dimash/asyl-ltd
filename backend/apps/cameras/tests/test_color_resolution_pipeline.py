"""Unknown colour/brand bags: import evidence, resolve, post and assign."""

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.cameras import (
    ai,
    analytics,
    color_resolution,
    event_sync,
    production,
)
from apps.cameras.models import (
    ANALYTICS_SCOPE_AI247,
    AlwaysOnColorProductMapping,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    AlwaysOnProductionRun,
    AlwaysOnStockBatch,
    AlwaysOnStockPosting,
    ContinuousCameraRole,
    MonoblockCameraSettings,
)
from apps.catalog.models import Product
from apps.eventlog.models import EventLog
from apps.warehouse.models import StockItem, StockReceipt

pytestmark = pytest.mark.django_db

ALMATY = ZoneInfo("Asia/Almaty")
# 13:00 local on 25.08 — business day 2026-08-25, posted after 19:00.
DAY_START = datetime(2026, 8, 25, 13, 0, tzinfo=ALMATY)
POST_AT = datetime(2026, 8, 25, 19, 5, tzinfo=ALMATY)
COLORS = {"R": "Red_50", "B": "Blue_50", "G": "Green_50", "W": "White_50"}


@pytest.fixture(autouse=True)
def _ai247_camera():
    ContinuousCameraRole.objects.create(
        camera="cam3", analytics_scope=ANALYTICS_SCOPE_AI247
    )
    MonoblockCameraSettings.objects.update_or_create(
        singleton=True,
        defaults={"always_on_camera_sources": ["cam3"]},
    )


def _raw(
    event_id: int,
    token: str,
    *,
    at: datetime,
    brand: str = "korol",
    votes: list[tuple[str, str]] | None = None,
) -> dict:
    """One /events row as cv-service 1dc2431 serialises it."""

    unknown = token == "?"
    color = "unknown" if unknown else COLORS[token]
    event = {
        "id": event_id,
        "created_at": at.isoformat(),
        "cam": "cam3",
        "source": "sub",
        "mode": "always_on",
        "analytics_scope": ANALYTICS_SCOPE_AI247,
        "class_name": "Bag_50",
        "total_after": event_id,
        "color": color,
        "color_confidence": 0.0 if unknown else 0.97,
        "brand": "unknown" if unknown else brand,
        "brand_confidence": 0.0 if unknown else 0.9,
        "sku": f"{color.split('_')[0].lower()}_{'unknown' if unknown else brand}",
        "classification_status": "needs_review" if unknown else "recognized",
    }
    if votes is not None:
        event["verification"] = {
            "version": 1,
            "reason": "track_ended",
            "minimum_votes": 2,
            "expected_lines": ["count", "before", "after"],
            "observed_lines": ["count", "before"],
            "distinct_frames": len(votes),
            "samples": [
                {
                    "frame": 100 + index,
                    "line_ids": ["count"],
                    "bbox": [1, 2, 3, 4],
                    "crop_size": [100, 100],
                    "prediction": {
                        "color": vote_color,
                        "color_confidence": 0.8,
                        "brand": vote_brand,
                        "brand_confidence": 0.7,
                        "sku": "x",
                        "classification_status": "needs_review",
                    },
                    "latency_ms": 10.0,
                    "error": None,
                }
                for index, (vote_color, vote_brand) in enumerate(votes)
            ],
        }
    return event


def _sequence(
    pattern: str, *, start: datetime = DAY_START, step: int = 10, first_id: int = 1
):
    return [
        _raw(first_id + index, token, at=start + timedelta(seconds=index * step))
        for index, token in enumerate(pattern.split())
    ]


def _page(events: list[dict]) -> dict:
    return {
        "events": events,
        "next_after_id": events[-1]["id"],
        "has_more": False,
        "enrichment_pending": False,
    }


def _import(events: list[dict]) -> None:
    with patch.object(ai, "count_events", return_value=_page(events)):
        result = event_sync.sync_camera("cam3")
    assert result.caught_up


def _event(event_id: int) -> AlwaysOnImportedEvent:
    return AlwaysOnImportedEvent.objects.get(camera="cam3", upstream_event_id=event_id)


def _product(color: str) -> Product:
    return Product.objects.create(
        name=f"Мешок {color}", color=color, weight_kg="50", price="100"
    )


def _map(color: str) -> Product:
    product = _product(color.capitalize())
    AlwaysOnColorProductMapping.objects.create(
        camera="cam3", color=color, product=product
    )
    return product


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def test_import_keeps_compact_votes_and_resolves_from_neighbours():
    events = _sequence("R R ? R")
    events[2] = _raw(
        3,
        "?",
        at=DAY_START + timedelta(seconds=20),
        votes=[("Red_50", "korol"), ("Blue_50", "unknown")],
    )
    _import(events)

    unknown = _event(3)
    assert unknown.color == "unknown" and unknown.brand == "unknown"  # never rewritten
    assert unknown.verification_votes == {
        "color": {"blue": 1, "red": 1},
        "brand": {"korol": 1},
        "frames": 2,
        "reason": "track_ended",
    }
    assert (unknown.resolved_color, unknown.color_resolution) == ("red", "neighbors")
    assert (unknown.resolved_brand, unknown.brand_resolution) == ("korol", "neighbors")
    assert "до: red" in unknown.resolution_note["color"]
    classified = _event(1)
    assert (classified.color_resolution, classified.brand_resolution) == ("", "")
    assert classified.verification_votes == {}


def test_a_later_page_resolves_an_earlier_unknown_bag():
    _import(_sequence("R ?"))
    assert _event(2).color_resolution == "unresolved"

    _import(_sequence("R R", start=DAY_START + timedelta(seconds=20), first_id=3))

    assert (_event(2).resolved_color, _event(2).color_resolution) == (
        "red",
        "neighbors",
    )


def test_resolution_failure_never_blocks_the_event_journal():
    with patch.object(
        color_resolution, "resolve_after_import", side_effect=RuntimeError("boom")
    ):
        _import(_sequence("R R ? R"))

    assert AlwaysOnCounterCursor.objects.get(camera="cam3").last_event_id == 4
    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 4
    assert _event(3).color_resolution == "unresolved"


def test_white_reverse_bags_are_not_brand_candidates():
    events = _sequence("W W")
    for event in events:
        event.update(
            brand="unknown", sku="white_reverse", classification_status="white_reverse"
        )
    _import(events)

    assert set(
        AlwaysOnImportedEvent.objects.values_list("brand_resolution", flat=True)
    ) == {""}


def test_a_bag_resolved_to_white_gets_no_brand_from_farther_bags():
    # White bags are turned face down: their brand is never visible, so the
    # red «korol» bags behind them must not lend their brand to a white bag.
    events = _sequence("R R W W ? W W")
    for event in events:
        if event["color"] == COLORS["W"]:
            event.update(
                brand="unknown",
                sku="white_reverse",
                classification_status="white_reverse",
            )
    _import(events)

    bag = _event(5)
    assert (bag.resolved_color, bag.color_resolution) == ("white", "neighbors")
    assert (bag.resolved_brand, bag.brand_resolution) == ("", "unresolved")
    assert "бел" in bag.resolution_note["brand"]


def test_brand_is_resolved_independently_of_colour():
    events = _sequence("R R R R")
    events[2]["brand"] = "unknown"
    events[3]["brand"] = "mars"
    events[1]["brand"] = "mars"
    events[0]["brand"] = "mars"
    _import(events)

    bag = _event(3)
    assert bag.color_resolution == ""  # the colour was classified
    assert (bag.resolved_brand, bag.brand_resolution) == ("mars", "neighbors")


def test_page_without_unknown_bags_adds_one_bounded_lookup():
    _import(_sequence("R R"))
    with CaptureQueriesContext(connection) as queries:
        _import(_sequence("R R", start=DAY_START + timedelta(minutes=1), first_id=3))
    journal = [
        q["sql"] for q in queries if '"cameras_alwaysonimportedevent"' in q["sql"]
    ]
    # existing-id read, bulk insert, and one indexed "anything to resolve?".
    assert len(journal) == 3


def test_repeated_passes_change_nothing_once_decided():
    events = _sequence("R R ? R ? B B ? ?")
    events[4] = _raw(
        5, "?", at=DAY_START + timedelta(seconds=40), votes=[("Blue_50", "korol")]
    )
    _import(events)
    fields = ("resolved_color", "color_resolution", "resolved_brand", "resolution_note")
    before = list(AlwaysOnImportedEvent.objects.order_by("id").values_list(*fields))

    assert color_resolution.resolve_business_day("cam3", DAY_START.date()) == 0
    assert color_resolution.resolve_business_day("cam3", DAY_START.date()) == 0
    assert color_resolution.resolve_after_import("cam3", DAY_START) == 0

    after = list(AlwaysOnImportedEvent.objects.order_by("id").values_list(*fields))
    assert after == before
    assert [_event(3).color_resolution, _event(5).color_resolution] == [
        "neighbors",
        "votes",
    ]
    # The trailing unknowns follow a homogeneous blue run.
    assert {_event(8).resolved_color, _event(9).resolved_color} == {"blue"}


# ---------------------------------------------------------------------------
# Totals and stock posting
# ---------------------------------------------------------------------------


def _ready_to_post():
    """The journal was caught up after the 19:00 cutoff (see _post_one)."""

    AlwaysOnCounterCursor.objects.filter(camera="cam3").update(
        event_caught_up_at=POST_AT
    )


def _post() -> dict:
    _ready_to_post()
    [batch] = production.post_due_stock(POST_AT)
    return batch


def test_day_totals_move_resolved_bags_into_their_colour():
    _import(_sequence("R R ? R ? B B"))
    # The second ? sits on a red/blue boundary at equal distance: unresolved.
    totals = production._day_totals("cam3", DAY_START.date())

    assert totals["red"] == {
        "detected_bags": 3,
        "resolved_bags": 1,
        "correction_bags": 0,
        "net_bags": 4,
        # Neighbour decisions may still change before posting.
        "provisional_bags": 1,
        "inferred": {"neighbors": 1},
    }
    assert totals["unknown"]["detected_bags"] == 2
    assert totals["unknown"]["resolved_bags"] == -1
    assert totals["unknown"]["provisional_bags"] == -1
    assert totals["unknown"]["net_bags"] == 1
    # Runs keep the camera's answers; nothing in the raw ledger moved.
    assert (
        sum(
            run.model_bags
            for run in AlwaysOnProductionRun.objects.filter(color="unknown")
        )
        == 2
    )


def test_resolved_bags_are_posted_under_their_colour():
    red = _map("red")
    blue = _map("blue")
    _import(_sequence("R R ? R B B"))

    batch = _post()

    assert batch["status"] == AlwaysOnStockBatch.POSTED
    assert batch["total_bags"] == 6
    assert batch["pending_bags"] == 0
    assert StockItem.objects.get(product=red).bags == 4
    assert StockItem.objects.get(product=blue).bags == 2
    item = AlwaysOnStockPosting.objects.get(color="red")
    assert (
        item.detected_bags,
        item.resolved_bags,
        item.correction_bags,
        item.posted_bags,
    ) == (3, 1, 0, 4)


def test_unresolved_bags_never_block_the_rest_of_the_shift():
    red = _map("red")
    blue = _map("blue")
    _import(_sequence("R R ? B B"))  # equidistant boundary, no votes

    batch = _post()

    assert batch["status"] == AlwaysOnStockBatch.POSTED
    assert batch["last_error"] == ""
    assert "unknown" not in batch["last_error"]
    assert batch["total_bags"] == 4
    assert batch["pending_bags"] == 1
    assert StockItem.objects.get(product=red).bags == 2
    assert StockItem.objects.get(product=blue).bags == 2
    assert not AlwaysOnStockPosting.objects.filter(color="unknown").exists()
    posted = EventLog.objects.get(event_type="always_on_stock_posted")
    assert posted.payload["pending_bags"] == 1


def test_a_shift_of_only_unresolved_bags_closes_with_them_pending():
    _import(_sequence("? ?"))

    batch = _post()

    assert batch["status"] == AlwaysOnStockBatch.EMPTY
    assert batch["pending_bags"] == 2
    assert not StockReceipt.objects.exists()


def test_a_failing_resolution_pass_never_blocks_the_shift_posting():
    red = _map("red")
    _map("blue")
    _import(_sequence("R R ? R ? B B"))  # first ? settled at import already

    with patch.object(
        color_resolution, "resolve_business_day", side_effect=RuntimeError("boom")
    ):
        batch = _post()

    # Decisions taken at import still count; the rest waits for an operator.
    assert batch["status"] == AlwaysOnStockBatch.POSTED
    assert batch["pending_bags"] == 1
    assert StockItem.objects.get(product=red).bags == 4


def test_votes_decide_a_run_boundary_before_posting():
    red = _map("red")
    blue = _map("blue")
    events = _sequence("R R ? B B")
    events[2] = _raw(
        3, "?", at=DAY_START + timedelta(seconds=20), votes=[("Blue_50", "korol")]
    )
    _import(events)

    batch = _post()

    assert batch["pending_bags"] == 0
    assert StockItem.objects.get(product=red).bags == 2
    assert StockItem.objects.get(product=blue).bags == 3
    assert _event(3).color_resolution == "votes"


def test_bag_just_before_the_cutoff_borrows_from_both_shifts():
    red = _map("red")
    _map("blue")
    cutoff = datetime(2026, 8, 25, 19, 0, tzinfo=ALMATY)
    # B B ? | R R — the unknown bag is 2 s before 19:00, the red run starts
    # 3 s after it (next shift); blue is 30 s back: red is clearly nearer.
    events = [
        _raw(1, "B", at=cutoff - timedelta(seconds=42)),
        _raw(2, "B", at=cutoff - timedelta(seconds=32)),
        _raw(3, "?", at=cutoff - timedelta(seconds=2)),
        _raw(4, "R", at=cutoff + timedelta(seconds=1)),
        _raw(5, "R", at=cutoff + timedelta(seconds=11)),
    ]
    _import(events)

    batch = _post()

    assert (_event(3).resolved_color, _event(3).color_resolution) == (
        "red",
        "neighbors",
    )
    assert batch["pending_bags"] == 0
    # Only this shift's bag is received; the next shift's red run waits.
    assert StockItem.objects.get(product=red).bags == 1
    assert batch["total_bags"] == 3


def test_a_long_stretch_without_colour_is_left_for_an_operator():
    red = _map("red")
    # Two red bags, then 30 minutes of bags the camera could not colour:
    # a systematic problem (new product, dirty lens), never a guess.
    _import(_sequence("R R " + " ".join(["?"] * 180)))

    batch = _post()

    assert batch["status"] == AlwaysOnStockBatch.POSTED
    assert batch["pending_bags"] == 180
    assert StockItem.objects.get(product=red).bags == 2


def test_a_long_stretch_between_two_runs_of_one_colour_is_not_filled_in():
    red = _map("red")
    _import(_sequence("R R " + " ".join(["?"] * 180) + " R R"))

    batch = _post()

    assert batch["pending_bags"] == 180
    assert StockItem.objects.get(product=red).bags == 4
    assert "подряд" in _event(3).resolution_note["color"]


def test_posting_waits_for_the_after_neighbours_of_a_bag_near_the_cutoff():
    red = _map("red")
    blue = _map("blue")
    cutoff = datetime(2026, 8, 25, 19, 0, tzinfo=ALMATY)
    _import(
        [
            _raw(1, "B", at=cutoff - timedelta(seconds=90)),
            _raw(2, "B", at=cutoff - timedelta(seconds=80)),
            _raw(3, "?", at=cutoff - timedelta(seconds=10)),
        ]
    )
    # Only blue before it so far: provisionally blue.
    assert _event(3).resolved_color == "blue"
    early = cutoff + timedelta(seconds=90)
    AlwaysOnCounterCursor.objects.filter(camera="cam3").update(
        event_caught_up_at=early
    )

    [waiting] = production.post_due_stock(early)

    # Its "after" neighbour may still be on its way: do not freeze a guess.
    assert waiting["status"] == AlwaysOnStockBatch.BLOCKED
    assert not StockReceipt.objects.exists()

    _import(
        [
            _raw(4, "R", at=cutoff + timedelta(seconds=2)),
            _raw(5, "R", at=cutoff + timedelta(seconds=12)),
        ]
    )
    later = cutoff + color_resolution.configured_max_gap() + timedelta(seconds=5)
    AlwaysOnCounterCursor.objects.filter(camera="cam3").update(
        event_caught_up_at=later
    )
    [batch] = production.post_due_stock(later)

    assert batch["status"] == AlwaysOnStockBatch.POSTED
    assert _event(3).resolved_color == "red"
    assert StockItem.objects.get(product=blue).bags == 2
    assert StockItem.objects.get(product=red).bags == 1


def test_posting_does_not_wait_without_unknown_bags_near_the_cutoff():
    blue = _map("blue")
    cutoff = datetime(2026, 8, 25, 19, 0, tzinfo=ALMATY)
    _import(
        [
            _raw(1, "B", at=cutoff - timedelta(seconds=30)),
            _raw(2, "B", at=cutoff - timedelta(seconds=20)),
        ]
    )
    early = cutoff + timedelta(seconds=90)
    AlwaysOnCounterCursor.objects.filter(camera="cam3").update(
        event_caught_up_at=early
    )

    [batch] = production.post_due_stock(early)

    assert batch["status"] == AlwaysOnStockBatch.POSTED
    assert StockItem.objects.get(product=blue).bags == 2


def test_long_brand_notes_do_not_rewrite_decisions_on_every_pass():
    long_left, long_right = "a" * 100, "b" * 100
    events = _sequence("R R ? R R")
    for event in events[:2]:
        event["brand"] = long_left
    for event in events[3:]:
        event["brand"] = long_right
    events[2] = _raw(
        3,
        "?",
        at=DAY_START + timedelta(seconds=20),
        votes=[("Red_50", long_left), ("Red_50", long_right)],
    )
    _import(events)
    assert len(_event(3).resolution_note["brand"]) == 300

    assert color_resolution.resolve_business_day("cam3", DAY_START.date()) == 0
    assert color_resolution.resolve_after_import("cam3", DAY_START) == 0


def test_posting_resolves_bags_written_without_markers_by_an_old_image():
    red = _map("red")
    _import(_sequence("R R R R"))
    # An image rollback inserts rows without the resolution columns.
    AlwaysOnImportedEvent.objects.filter(upstream_event_id=3).update(color="unknown")
    AlwaysOnProductionRun.objects.all().delete()
    production.record_color_deltas(
        "cam3", {"red": 2}, DAY_START, 2, ordered_color_event=True
    )
    production.record_color_deltas(
        "cam3",
        {"unknown": 1},
        DAY_START + timedelta(seconds=20),
        1,
        ordered_color_event=True,
    )
    production.record_color_deltas(
        "cam3",
        {"red": 1},
        DAY_START + timedelta(seconds=30),
        1,
        ordered_color_event=True,
    )

    batch = _post()

    assert batch["pending_bags"] == 0
    assert StockItem.objects.get(product=red).bags == 4


def test_correction_on_unknown_is_never_moved_twice():
    _map("red")
    # Two bags after a 6-minute pause: nothing to borrow from yet.
    _import(
        _sequence("R R")
        + _sequence("? ?", start=DAY_START + timedelta(minutes=6), first_id=3)
    )
    AlwaysOnCounterCursor.objects.filter(camera="cam3").update(
        event_sync_supported=False
    )
    with patch.object(
        production.timezone, "now", return_value=DAY_START + timedelta(hours=1)
    ):
        production.record_correction("cam3", "unknown", 1, "двойной счёт мешка")
    # A red run follows: both unknown bags now resolve to red.
    _import(
        _sequence("R R", start=DAY_START + timedelta(minutes=6, seconds=20), first_id=5)
    )
    assert [_event(3).color_resolution, _event(4).color_resolution] == ["neighbors"] * 2

    totals = production._day_totals("cam3", DAY_START.date())

    # Only the one bag the correction left in "unknown" can move to red.
    assert totals["unknown"]["net_bags"] == 0
    assert totals["red"]["resolved_bags"] == 1
    assert totals["red"]["net_bags"] == 5


def test_correction_availability_leaves_out_automatic_decisions():
    # Neighbour/vote decisions can still change until the shift is posted, so
    # a correction may only subtract bags that cannot move away later.
    _import(_sequence("R R ? R"))
    AlwaysOnCounterCursor.objects.filter(camera="cam3").update(
        event_sync_supported=False
    )
    with patch.object(
        production.timezone, "now", return_value=DAY_START + timedelta(hours=1)
    ):
        with pytest.raises(ValidationError) as error:
            production.record_correction("cam3", "red", 4, "брак всей партии")
        assert "3" in str(error.value.detail["amount"])
        assert "автоматически" in str(error.value.detail["amount"])
        production.record_correction("cam3", "red", 3, "брак всей партии")
        with pytest.raises(ValidationError):
            production.record_correction("cam3", "red", 2, "лишний мешок")

    totals = production._day_totals("cam3", DAY_START.date())
    assert totals["red"]["net_bags"] == 1
    assert totals["red"]["provisional_bags"] == 1


def test_a_correction_survives_a_later_change_of_an_automatic_decision():
    red = _map("red")
    blue = _map("blue")
    _import(_sequence("R R ?"))
    assert _event(3).resolved_color == "red"  # one-sided, still provisional
    with patch.object(
        production.timezone, "now", return_value=DAY_START + timedelta(hours=1)
    ):
        # The unknown bag may still move away: only the camera's two count.
        with pytest.raises(ValidationError):
            production.record_correction("cam3", "red", 3, "брак всей партии")
        production.record_correction("cam3", "red", 2, "брак двух мешков")
    # Blue bags arrive right after the unknown one: it is now blue.
    _import(
        [
            _raw(4, "B", at=DAY_START + timedelta(seconds=22)),
            _raw(5, "B", at=DAY_START + timedelta(seconds=32)),
        ]
    )
    assert _event(3).resolved_color == "blue"

    batch = _post()

    assert batch["status"] == AlwaysOnStockBatch.POSTED
    assert batch["last_error"] == ""
    assert not StockItem.objects.filter(product=red).exists() or (
        StockItem.objects.get(product=red).bags == 0
    )
    assert StockItem.objects.get(product=blue).bags == 3


def test_a_correction_on_unknown_may_remove_an_automatically_resolved_bag():
    red = _map("red")
    _import(_sequence("R R ? R"))
    with patch.object(
        production.timezone, "now", return_value=DAY_START + timedelta(hours=1)
    ):
        production.record_correction("cam3", "unknown", 1, "двойной счёт мешка")

    batch = _post()

    assert batch["status"] == AlwaysOnStockBatch.POSTED
    assert batch["pending_bags"] == 0
    assert StockItem.objects.get(product=red).bags == 3


def test_auto_resolution_never_changes_a_posted_shift():
    _map("red")
    _map("blue")
    _import(_sequence("R R ? B B"))
    _post()
    before = _event(3)
    assert before.color_resolution == "unresolved"

    # New evidence would now decide the bag, but its shift is already posted.
    AlwaysOnImportedEvent.objects.filter(upstream_event_id=4).update(color="Red_50")
    color_resolution.resolve_business_day("cam3", DAY_START.date())
    color_resolution.resolve_after_import("cam3", DAY_START)

    after = _event(3)
    assert (after.resolved_color, after.color_resolution) == ("", "unresolved")


def test_posting_query_count_does_not_grow_with_unknown_bags():
    _map("red")

    def measured(pattern: str, first_id: int, start: datetime) -> int:
        _import(_sequence(pattern, start=start, first_id=first_id))
        AlwaysOnImportedEvent.objects.update(color_resolution="unresolved")
        AlwaysOnImportedEvent.objects.filter(color__iexact="red_50").update(
            color_resolution=""
        )
        with CaptureQueriesContext(connection) as queries:
            color_resolution.resolve_business_day("cam3", start.date())
            production._day_totals("cam3", start.date())
        return len(queries)

    small = measured("R ? R", 1, DAY_START)
    # Many unknown bags, every streak short enough to be resolved (and saved).
    large = measured(
        "R ? ? ? ? ? R ? ? ? R ? ? R", 10, DAY_START + timedelta(days=1)
    )
    assert large == small


# ---------------------------------------------------------------------------
# Manual assignment («Указать цвет»)
# ---------------------------------------------------------------------------


def _assign(**overrides) -> dict:
    options = {
        "camera": "cam3",
        "business_day": DAY_START.date().isoformat(),
        "color": "red",
        "bags": 1,
        "reason": "Проверено по записи камеры",
        "user": None,
    } | overrides
    return production.assign_unknown_color(**options)


def test_manual_colour_after_posting_is_received_and_audited(boss):
    red = _map("red")
    _map("blue")
    _import(_sequence("R R ? B B"))
    batch = _post()
    assert batch["pending_bags"] == 1

    payload = _assign(user=boss)

    stored = AlwaysOnStockBatch.objects.get(pk=batch["id"])
    assert (stored.status, stored.total_bags, stored.pending_bags) == ("posted", 5, 0)
    assert StockItem.objects.get(product=red).bags == 3
    manual = AlwaysOnStockPosting.objects.get(kind=AlwaysOnStockPosting.MANUAL_COLOR)
    assert (
        manual.color,
        manual.product_id,
        manual.posted_bags,
        manual.resolved_bags,
    ) == ("red", red.pk, 1, 1)
    assert manual.created_by == boss
    assert manual.receipt.bags == 1
    bag = _event(3)
    assert (bag.color, bag.resolved_color, bag.color_resolution) == (
        "unknown",
        "red",
        "manual",
    )
    assert "Проверено по записи камеры" in bag.resolution_note["color"]
    audit = EventLog.objects.get(event_type="always_on_unknown_color_assigned")
    assert audit.user == boss
    assert audit.payload["bags"] == 1 and audit.payload["events"] == [3]
    assert audit.payload["receipt"] == manual.receipt_id
    # The command answers with the fresh production snapshot for the screen.
    [row] = [item for item in payload["batches"] if item["id"] == batch["id"]]
    assert row["pending_bags"] == 0 and row["total_bags"] == 5
    assert {item["kind"] for item in row["items"]} == {"shift", "manual_color"}


def test_manual_colour_turns_an_empty_shift_into_a_posted_one():
    red = _map("red")
    _import(_sequence("? ?"))
    batch = _post()
    assert batch["status"] == "empty"

    _assign(bags=2)

    stored = AlwaysOnStockBatch.objects.get(pk=batch["id"])
    assert (stored.status, stored.total_bags, stored.pending_bags) == ("posted", 2, 0)
    assert StockItem.objects.get(product=red).bags == 2


def test_manual_colour_cannot_exceed_pending_bags():
    _map("red")
    _map("blue")
    _import(_sequence("R R ? B B"))
    _post()

    with pytest.raises(ValidationError) as error:
        _assign(bags=2)
    assert "1" in str(error.value.detail["bags"])
    assert not AlwaysOnStockPosting.objects.filter(kind="manual_color").exists()


def test_manual_colour_needs_a_product_for_that_colour():
    _map("red")
    _map("blue")
    _import(_sequence("R R ? B B"))
    _post()

    with pytest.raises(ValidationError) as error:
        _assign(color="green")
    assert "color" in error.value.detail
    with pytest.raises(ValidationError):
        _assign(color="unknown")
    assert _event(3).color_resolution == "unresolved"


def test_manual_colour_before_posting_joins_the_shift_posting():
    red = _map("red")
    _map("blue")
    _import(_sequence("R R ? B B"))
    with patch.object(
        production.timezone, "now", return_value=DAY_START + timedelta(hours=1)
    ):
        payload = _assign()
    assert not StockReceipt.objects.exists()  # nothing posted yet
    assert payload["unresolved"] == {"business_day": "2026-08-25", "bags": 0}
    [red_preview] = [row for row in payload["preview"] if row["color"] == "red"]
    assert red_preview["net_bags"] == 3 and red_preview["inferred"] == {"manual": 1}

    batch = _post()

    assert batch["pending_bags"] == 0
    assert StockItem.objects.get(product=red).bags == 3
    assert not AlwaysOnStockPosting.objects.filter(kind="manual_color").exists()


def test_blocked_shift_keeps_its_bags_without_colour_visible_and_assignable():
    red = _map("red")
    blue_product = _product("Blue")
    _import(_sequence("R R ? B B"))

    batch = _post()

    # Only the real configuration gap is reported, never «unknown».
    assert batch["status"] == AlwaysOnStockBatch.BLOCKED
    assert batch["last_error"] == "Не настроен товар для цветов: blue"
    assert batch["pending_bags"] == 1

    with patch.object(production.timezone, "now", return_value=POST_AT):
        payload = _assign()
    [row] = [item for item in payload["batches"] if item["id"] == batch["id"]]
    assert row["pending_bags"] == 0 and row["status"] == AlwaysOnStockBatch.BLOCKED
    assert not StockReceipt.objects.exists()  # it joins the shift posting

    AlwaysOnColorProductMapping.objects.create(
        camera="cam3", color="blue", product=blue_product
    )
    retried = production.retry_batch(batch["id"])

    assert retried["status"] == AlwaysOnStockBatch.POSTED
    assert retried["pending_bags"] == 0
    assert StockItem.objects.get(product=red).bags == 3
    assert StockItem.objects.get(product=blue_product).bags == 2


def _at(seconds: int) -> datetime:
    return DAY_START + timedelta(seconds=seconds)


def test_a_manual_colour_is_never_evidence_for_other_bags():
    # «Указать цвет» is a count, not a check of particular bags: labelling
    # N bags must change exactly N bags, never their neighbours.
    red = _map("red")
    _map("blue")
    _map("green")
    _import(
        [
            _raw(1, "B", at=_at(0)),
            _raw(2, "B", at=_at(10)),
            _raw(3, "?", at=_at(110)),
            _raw(4, "?", at=_at(115)),
            _raw(5, "G", at=_at(215)),
            _raw(6, "G", at=_at(225)),
        ]
    )
    assert {_event(3).color_resolution, _event(4).color_resolution} == {"unresolved"}
    with patch.object(
        production.timezone, "now", return_value=DAY_START + timedelta(hours=1)
    ):
        payload = _assign(bags=1)
    assert payload["unresolved"]["bags"] == 1
    color_resolution.resolve_business_day("cam3", DAY_START.date())

    assert (_event(3).resolved_color, _event(3).color_resolution) == ("red", "manual")
    assert (_event(4).resolved_color, _event(4).color_resolution) == ("", "unresolved")
    batch = _post()
    assert batch["pending_bags"] == 1
    assert StockItem.objects.get(product=red).bags == 1


def test_a_manual_colour_never_resolves_the_rest_of_a_boundary():
    green = _map("green")
    _map("red")
    _map("blue")
    _import(
        [
            _raw(1, "R", at=_at(0)),
            _raw(2, "R", at=_at(10)),
            _raw(3, "?", at=_at(20)),
            _raw(4, "?", at=_at(21)),
            _raw(5, "B", at=_at(31)),
            _raw(6, "B", at=_at(41)),
            _raw(7, "?", at=_at(3600)),
        ]
    )
    with patch.object(
        production.timezone, "now", return_value=DAY_START + timedelta(hours=2)
    ):
        _assign(color="green", bags=1)
    color_resolution.resolve_business_day("cam3", DAY_START.date())

    totals = production._day_totals("cam3", DAY_START.date())
    assert totals["green"]["net_bags"] == 1
    assert totals["unknown"]["net_bags"] == 2
    batch = _post()
    assert batch["pending_bags"] == 2
    assert StockItem.objects.get(product=green).bags == 1


def test_a_later_automatic_decision_never_displaces_a_manual_colour():
    red = _map("red")
    blue = _map("blue")
    _import(
        [
            _raw(1, "?", at=_at(0)),
            _raw(2, "?", at=_at(1800)),
            _raw(3, "?", at=_at(3600)),
        ]
    )
    with patch.object(
        production.timezone, "now", return_value=DAY_START + timedelta(hours=2)
    ):
        production.record_correction("cam3", "unknown", 1, "двойной счёт мешка")
        payload = _assign(bags=2)
    assert payload["unresolved"]["bags"] == 0
    # A blue run now arrives next to the third bag and resolves it.
    _import([_raw(4, "B", at=_at(3610)), _raw(5, "B", at=_at(3620))])
    assert _event(3).color_resolution == "neighbors"

    totals = production._day_totals("cam3", DAY_START.date())
    assert totals["red"]["net_bags"] == 2
    assert totals["red"]["inferred"] == {"manual": 2}
    assert totals["blue"]["net_bags"] == 2
    assert totals["unknown"]["net_bags"] == 0
    _post()
    assert StockItem.objects.get(product=red).bags == 2
    assert StockItem.objects.get(product=blue).bags == 2


def test_assign_api_requires_the_manage_permission(auth_client, admin_user, boss):
    _map("red")
    _map("blue")
    _import(_sequence("R R ? B B"))
    _post()
    body = {
        "camera": "cam3",
        "business_day": "2026-08-25",
        "color": "red",
        "bags": 1,
        "reason": "Проверено по записи камеры",
    }

    denied = auth_client(boss).post(
        "/api/cameras/always-on-production/unknown-colors/", body, format="json"
    )
    assert denied.status_code == 403

    response = auth_client(admin_user).post(
        "/api/cameras/always-on-production/unknown-colors/", body, format="json"
    )
    assert response.status_code == 200
    assert response.data["camera"] == "cam3"
    assert AlwaysOnStockPosting.objects.filter(kind="manual_color").count() == 1

    invalid = auth_client(admin_user).post(
        "/api/cameras/always-on-production/unknown-colors/",
        body | {"bags": 5},
        format="json",
    )
    assert invalid.status_code == 400
    assert "bags" in invalid.data["detail"]


# ---------------------------------------------------------------------------
# Analytics: resolved colours shown with markers
# ---------------------------------------------------------------------------


def test_selected_day_runs_show_resolved_colours_with_markers():
    _import(_sequence("R R ? R"))

    payload = production.production_payload("cam3", day="2026-08-25")

    runs = [
        (run["color"], run["model_bags"], run.get("inferred"))
        for run in payload["day_runs"]
    ]
    assert runs == [("red", 2, None), ("red", 1, {"neighbors": 1}), ("red", 1, None)]
    assert payload["day_runs"][1]["source_color"] == "unknown"
    assert [
        (run["color"], run["model_bags"]) for run in payload["algorithm_day_runs"]
    ] == [("red", 4)]
    assert payload["run_smoothing"]["raw_model_per_color"] == {"red": 4}
    assert payload["color_resolution"] == {
        "inferred": {"red": {"neighbors": 1}},
        "unresolved_bags": 0,
    }
    # The durable ledger still says what the camera said.
    assert list(
        AlwaysOnProductionRun.objects.order_by("started_at").values_list(
            "color", flat=True
        )
    ) == [
        "red",
        "unknown",
        "red",
    ]


def test_selected_day_colour_cards_carry_the_resolution_marker():
    events = _sequence("R R ? R ? ? B B")
    events[4] = _raw(
        5, "?", at=DAY_START + timedelta(seconds=40), votes=[("Red_50", "korol")]
    )
    _import(events)

    smoothing = production.production_payload("cam3", day="2026-08-25")[
        "run_smoothing"
    ]

    for key in ("raw_colors", "algorithm_colors"):
        cards = {item["color"]: item for item in smoothing[key]}
        assert cards["red"]["inferred"] == {"neighbors": 1, "votes": 1}, key
        assert "inferred" not in cards["blue"], key
        assert "inferred" not in cards["unknown"], key


def test_selected_day_splits_an_unknown_run_at_a_resolved_boundary():
    events = _sequence("R R ? ? B B")
    events[2] = _raw(
        3, "?", at=DAY_START + timedelta(seconds=20), votes=[("Red_50", "korol")]
    )
    events[3] = _raw(
        4, "?", at=DAY_START + timedelta(seconds=30), votes=[("Blue_50", "korol")]
    )
    _import(events)

    runs = production.production_payload("cam3", day="2026-08-25")["day_runs"]

    assert [
        (run["color"], run["model_bags"], run.get("inferred"), run.get("segment"))
        for run in runs
    ] == [
        ("red", 2, None, None),
        ("red", 1, {"votes": 1}, 0),
        ("blue", 1, {"votes": 1}, 1),
        ("blue", 2, None, None),
    ]
    assert runs[1]["id"] == runs[2]["id"]


def test_unresolved_bags_stay_unknown_in_the_selected_day():
    _import(_sequence("R R ? B B"))

    payload = production.production_payload("cam3", day="2026-08-25")

    assert [run["color"] for run in payload["day_runs"]] == ["red", "unknown", "blue"]
    assert "inferred" not in payload["day_runs"][1]
    assert payload["color_resolution"] == {"inferred": {}, "unresolved_bags": 1}


def test_dominant_brand_uses_resolved_colour_and_brand():
    events = _sequence("R R ? R")
    for event in events:
        if event["color"] != "unknown":
            event["brand"] = "mars"
    _import(events)

    payload = production.production_payload("cam3", day="2026-08-25")

    assert payload["dominant_brand_by_color"] == {"red": "mars"}


def test_period_analytics_show_resolved_colours_and_keep_the_raw_ledger():
    _import(_sequence("R R ? R ? B B"))

    payload = analytics.today_payload(
        ANALYTICS_SCOPE_AI247,
        date_from=DAY_START.date(),
        date_to=DAY_START.date(),
    )

    camera = payload["cameras"][0]
    colors = {item["color"]: item for item in camera["colors"]}
    assert colors["red"]["total"] == 4
    assert colors["red"]["inferred"] == {"neighbors": 1}
    assert colors["unknown"]["total"] == 1
    assert "inferred" not in colors["blue"]
    [point] = [item for item in camera["history"] if item["day"] == "2026-08-25"]
    assert point["model_per_color"] == {"red": 4, "unknown": 1, "blue": 2}
    assert {item["color"]: item["total"] for item in payload["colors"]}["red"] == 4
    brands = {item["brand"]: item["total"] for item in camera["brands"]}
    # Brand is resolved independently: korol on both sides of the boundary.
    assert brands == {"korol": 7}
    stored = AlwaysOnDailyAnalytics.objects.get(camera="cam3")
    assert stored.model_per_color == {"red": 3, "unknown": 2, "blue": 2}


def test_period_analytics_overlay_is_one_query_for_any_range():
    _import(_sequence("R R ? R"))
    _import(_sequence("B B ? B", start=DAY_START + timedelta(days=1), first_id=5))

    def count(date_to) -> int:
        with CaptureQueriesContext(connection) as queries:
            analytics.today_payload(
                ANALYTICS_SCOPE_AI247, date_from=DAY_START.date(), date_to=date_to
            )
        return len(queries)

    assert count(DAY_START.date()) == count(DAY_START.date() + timedelta(days=1))


def test_selected_day_query_count_does_not_grow_with_unknown_runs():
    _import(_sequence("R ? R R"))
    _import(
        _sequence(
            "R ? R R ? R ? B B ? B ? ? R R",
            start=DAY_START + timedelta(days=1),
            first_id=5,
        )
    )

    def count(day: str) -> int:
        with CaptureQueriesContext(connection) as queries:
            production.production_payload("cam3", day=day)
        return len(queries)

    assert count("2026-08-26") == count("2026-08-25")


def test_period_analytics_overlay_respects_an_archive_of_the_live_day(boss):
    """Bags archived with the live day never move bags counted after it."""

    clock = {"now": DAY_START + timedelta(minutes=5)}
    with patch("django.utils.timezone.now", side_effect=lambda: clock["now"]):
        _import(_sequence("R R ? R"))
        assert _event(3).color_resolution == "neighbors"
        clock["now"] += timedelta(seconds=30)
        analytics.archive_camera("cam3", "конец партии", boss)
        # Two lone bags after the archive: nothing to borrow a colour from.
        clock["now"] = DAY_START + timedelta(minutes=40)
        _import(
            _sequence(
                "? ?", start=DAY_START + timedelta(minutes=20), step=600, first_id=5
            )
        )
        payload = analytics.today_payload(
            ANALYTICS_SCOPE_AI247,
            date_from=DAY_START.date(),
            date_to=DAY_START.date(),
        )

    camera = payload["cameras"][0]
    assert {item["color"]: item["total"] for item in camera["colors"]} == {
        "unknown": 2
    }
    assert all("inferred" not in item for item in camera["colors"])


def test_subtract_today_counts_manual_but_not_automatic_colours(boss):
    now = timezone.now()
    start = now - timedelta(minutes=5)
    _map("red")
    _import(_sequence("R R ? R ? ? ? ? ? ?", start=start, step=1))
    AlwaysOnCounterCursor.objects.filter(camera="cam3").update(
        event_sync_supported=False
    )
    # The six trailing bags are too many in a row to guess; one is assigned.
    production.assign_unknown_color(
        "cam3",
        production.business_day_for(now),
        "red",
        1,
        "Проверено по записи камеры",
    )

    with pytest.raises(ValidationError) as error:
        analytics.subtract_today("cam3", 5, "весь красный брак", boss, "red")
    assert "4" in str(error.value.detail["amount"])
    row = analytics.subtract_today("cam3", 4, "весь красный брак", boss, "red")

    assert row["total"] == 6
