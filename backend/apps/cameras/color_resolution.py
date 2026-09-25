"""Neighbour/vote resolution of AI 24/7 bags the camera left without a colour.

Multi-line verification (cv-service ``bag_verification.consensus``) confirms a
colour only by a strict majority of at least two frames; otherwise the counted
bag arrives as colour and/or brand ``unknown``. Such a bag is still a real bag
on the belt, and the bags right before and after it almost always belong to
the same production run. This module decides, deterministically and without
ever overwriting the camera's answer, which colour/brand an unknown bag gets.

* ``resolve_sequence`` is a pure function over plain ordered sequences.
* The database passes store the decision in ``resolved_*``/``*_resolution``
  of ``AlwaysOnImportedEvent``; events of a posted production shift are never
  changed automatically (a manual assignment goes through production.py).
* ``business_day_transfers`` (stock posting and previews through
  ``production_runs._day_totals``) and ``overlay_daily_rows`` (calendar-day
  analytics) are the only places that move resolved bags from the camera's
  ``unknown`` bucket to their resolved colour. The raw ledgers never change.
* Only the camera's own answers are evidence. A manual assignment is a count
  (not a check of particular bags), so it never resolves another bag; and a
  long streak of misses or a far neighbour is never guessed across.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from fractions import Fraction

from django.db.models import Q

from .event_protocol import UNKNOWN_CLASS, brand_key, event_color_key, normalize_brand
from .models import (
    ANALYTICS_SCOPE_AI247,
    METHOD_MANUAL,
    METHOD_NEIGHBORS,
    METHOD_UNRESOLVED,
    METHOD_VOTES,
    AlwaysOnCountArchive,
    AlwaysOnImportedEvent,
    AlwaysOnStockBatch,
)
from .production_runs import (
    TERMINAL_BATCH_STATUSES,
    _iso,
    business_day_for,
    local_date,
    local_day_window,
    scheduled_for,
)

UNKNOWN = UNKNOWN_CLASS
WHITE = "white"
WHITE_BRAND_NOTE = "белый мешок лежит обратной стороной: бренд не виден"
# Colours that are never posted to stock by a colour→product mapping: their
# bags wait for a resolved or manual colour instead of blocking the shift.
PENDING_COLORS = frozenset({UNKNOWN})

# ``""`` means the camera's own answer is used as is (a classified bag).
AUTO_METHODS = (METHOD_UNRESOLVED, METHOD_NEIGHBORS, METHOD_VOTES)
RESOLVED_METHODS = (METHOD_NEIGHBORS, METHOD_VOTES, METHOD_MANUAL)

# Longest pause that still belongs to one continuous stretch of bags.
MAX_GAP = timedelta(seconds=180)
# More bags in a row without the camera's answer than this is a systematic
# problem (a new product the model does not know, a dirty lens, lighting),
# not a random miss: the whole stretch waits for an operator.
MAX_STREAK = 5
NEARER_RATIO = Fraction(1, 3)
# Order in which resolved bags take a bucket's capacity: an audited manual
# assignment always keeps its bags, then decisions with vote evidence.
_METHOD_PRIORITY = {METHOD_MANUAL: 0, METHOD_VOTES: 1, METHOD_NEIGHBORS: 2}
# How far back an imported page can still change an earlier decision, and the
# extra continuous context loaded around every evaluated window.
IMPORT_LOOKBACK = timedelta(minutes=30)
WINDOW_CONTEXT = timedelta(minutes=30)


# ---------------------------------------------------------------------------
# Pure resolver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bag:
    """One counted bag in camera order.

    ``value`` is the camera's own colour/brand answer, the only evidence.
    Decisions — automatic or an operator's count-based assignment — are never
    fed back as ``value``: a run depends only on immutable camera evidence, is
    idempotent, and labelling N bags by hand can never move a neighbour.
    ``unknown`` marks a bag the camera left without an answer that is not a
    ``candidate`` (assigned by hand): it still counts in a streak of misses.
    """

    at: datetime
    value: str | None
    candidate: bool = False
    votes: Mapping[str, int] = field(default_factory=dict)
    unknown: bool = False


@dataclass(frozen=True)
class Resolution:
    value: str | None
    method: str
    note: str


_NONE = -1  # no continuous neighbour on this side
_TRUNCATED = -2  # the loaded window ends inside a continuous stretch


def _favourite(votes: Mapping[str, int]) -> str | None:
    positive = {key: int(count) for key, count in votes.items() if int(count) > 0}
    if not positive:
        return None
    best = max(positive.values())
    winners = sorted(key for key, count in positive.items() if count == best)
    return winners[0] if len(winners) == 1 else None


def _seconds(value: timedelta) -> str:
    return f"{int(round(value.total_seconds()))} с"


def _votes_text(votes: Mapping[str, int]) -> str:
    return ", ".join(f"{key} {count}" for key, count in sorted(votes.items()))


def resolve_sequence(
    bags: Sequence[Bag],
    *,
    max_gap: timedelta = MAX_GAP,
    max_streak: int = MAX_STREAK,
    open_start: bool = False,
    open_end: bool = False,
) -> list[Resolution | None]:
    """Decide every candidate bag from its nearest known neighbours.

    Rules for a candidate bag, per attribute:

    a) the nearest known bags before and after agree → that value;
    b) only one side exists → its value, if that side is a homogeneous run
       (its two nearest known bags agree) and partial votes do not favour a
       different value;
    c) the sides disagree (a run boundary) → the value the partial votes
       favour when it is one of the two; otherwise the clearly nearer
       neighbour (distance ratio ≤ ``NEARER_RATIO``); otherwise unresolved.

    A neighbour counts only when it is at most ``max_gap`` away from the bag
    itself (and so is reached without a longer pause); the second bag that
    confirms a one-sided run must be continuous with it. A stretch of more
    than ``max_streak`` bags in a row without the camera's answer is never
    filled in: that is a systematic failure, not a random miss.
    ``open_start``/``open_end`` mark window edges that cut a continuous
    stretch: a decision that would need data beyond them is ``None``
    (inconclusive), as is every non-candidate bag.
    """

    count = len(bags)
    linked = [False] + [
        timedelta(0) <= bags[index].at - bags[index - 1].at <= max_gap
        for index in range(1, count)
    ]

    previous = [_NONE] * count
    last = _TRUNCATED if open_start else _NONE
    for index in range(count):
        if index and not linked[index]:
            last = _NONE
        previous[index] = last
        if bags[index].value is not None:
            last = index

    following = [_NONE] * count
    last = _TRUNCATED if open_end else _NONE
    for index in range(count - 1, -1, -1):
        if index + 1 < count and not linked[index + 1]:
            last = _NONE
        following[index] = last
        if bags[index].value is not None:
            last = index

    # Continuous stretches of bags without a camera answer, and how many of
    # them the camera missed (legacy bags with no colour field do not count).
    stretch = [0] * count
    misses: list[int] = []
    for index, bag in enumerate(bags):
        if bag.value is not None:
            continue
        if index == 0 or bags[index - 1].value is not None or not linked[index]:
            misses.append(0)
        stretch[index] = len(misses) - 1
        if bag.candidate or bag.unknown:
            misses[-1] += 1

    decisions: list[Resolution | None] = [None] * count
    for index, bag in enumerate(bags):
        if not bag.candidate:
            continue
        streak = misses[stretch[index]]
        if streak > max_streak:
            # Also when the window cuts the stretch: it is at least this long.
            decisions[index] = Resolution(
                None,
                METHOD_UNRESOLVED,
                f"подряд {streak} меш. без ответа камеры (допустимо до "
                f"{max_streak}): похоже на сбой распознавания или новый товар",
            )
            continue
        decisions[index] = _decide(
            bags,
            index,
            previous,
            following,
            gap=max_gap,
        )
    return decisions


def _decide(
    bags: Sequence[Bag],
    index: int,
    previous: list[int],
    following: list[int],
    *,
    gap: timedelta,
) -> Resolution | None:
    bag = bags[index]
    before, after = previous[index], following[index]
    if _TRUNCATED in (before, after):
        return None
    # A continuous stretch can be long; only a close bag lends its value.
    if before != _NONE and bag.at - bags[before].at > gap:
        before = _NONE
    if after != _NONE and bags[after].at - bag.at > gap:
        after = _NONE
    votes = dict(bag.votes or {})
    favourite = _favourite(votes)
    vote_note = f" · голоса: {_votes_text(votes)}" if votes else ""

    if before == _NONE and after == _NONE:
        return Resolution(
            None,
            METHOD_UNRESOLVED,
            f"нет распознанных соседей ближе {_seconds(gap)}{vote_note}",
        )

    if before != _NONE and after != _NONE:
        left, right = bags[before], bags[after]
        left_distance = bag.at - left.at
        right_distance = right.at - bag.at
        evidence = (
            f"до: {left.value} ({_seconds(left_distance)}), "
            f"после: {right.value} ({_seconds(right_distance)})"
        )
        if left.value == right.value:
            return Resolution(left.value, METHOD_NEIGHBORS, evidence + vote_note)
        if favourite in (left.value, right.value):
            return Resolution(favourite, METHOD_VOTES, evidence + vote_note)
        near, far = sorted((left_distance, right_distance))
        near_us = near // timedelta(microseconds=1)
        far_us = far // timedelta(microseconds=1)
        if (
            far_us > 0
            and near_us * NEARER_RATIO.denominator <= far_us * NEARER_RATIO.numerator
        ):
            value = left.value if left_distance <= right_distance else right.value
            return Resolution(
                value, METHOD_NEIGHBORS, evidence + " · ближе" + vote_note
            )
        return Resolution(
            None, METHOD_UNRESOLVED, "граница партий: " + evidence + vote_note
        )

    side = before if before != _NONE else after
    second = previous[side] if before != _NONE else following[side]
    if second == _TRUNCATED:
        return None
    neighbour = bags[side]
    label = "до" if before != _NONE else "после"
    evidence = f"{label}: {neighbour.value} ({_seconds(abs(bag.at - neighbour.at))})"
    if second == _NONE or bags[second].value != neighbour.value:
        return Resolution(
            None,
            METHOD_UNRESOLVED,
            f"сосед только {label}, партия не подтверждена: {evidence}{vote_note}",
        )
    if favourite is not None and favourite != neighbour.value:
        return Resolution(
            None,
            METHOD_UNRESOLVED,
            f"голоса против соседа: {evidence}{vote_note}",
        )
    return Resolution(neighbour.value, METHOD_NEIGHBORS, evidence + vote_note)


# ---------------------------------------------------------------------------
# What a stored event means (single source for every reader)
# ---------------------------------------------------------------------------


def camera_color_key(color: str | None, class_name: str | None) -> str:
    """Bucket the camera's own answer was counted under in the run ledger."""

    return event_color_key(color, class_name) or "unclassified"


def effective_color_key(
    color: str | None,
    class_name: str | None,
    resolved_color: str,
    color_resolution: str,
) -> str:
    """Colour a bag counts as after resolution (camera answer otherwise)."""

    if color_resolution in RESOLVED_METHODS and resolved_color:
        return resolved_color
    return event_color_key(color, class_name)


def effective_brand(
    brand: str | None,
    resolved_brand: str,
    brand_resolution: str,
) -> str | None:
    """Brand key after resolution; ``None`` keeps legacy "no brand field"."""

    if brand_resolution in RESOLVED_METHODS and resolved_brand:
        return resolved_brand
    return brand_key(brand)


def _is_color_candidate(row: AlwaysOnImportedEvent) -> bool:
    return (
        row.color_resolution != METHOD_MANUAL
        and event_color_key(row.color, row.class_name) == UNKNOWN
    )


def _known_color(row: AlwaysOnImportedEvent) -> str | None:
    """The camera's own colour; a manual count is never neighbour evidence."""

    if row.color_resolution == METHOD_MANUAL:
        return None
    key = event_color_key(row.color, row.class_name)
    return key if key and key != UNKNOWN else None


def _is_unknown_brand(brand: str | None, classification_status: str | None) -> bool:
    # A white bag is shown reversed on purpose: no brand is visible by design.
    return brand_key(brand) == UNKNOWN and classification_status != "white_reverse"


def _is_brand_candidate(row: AlwaysOnImportedEvent) -> bool:
    return row.brand_resolution != METHOD_MANUAL and _is_unknown_brand(
        row.brand, row.classification_status
    )


def _known_brand(row: AlwaysOnImportedEvent) -> str | None:
    if row.brand_resolution == METHOD_MANUAL:
        return None
    return normalize_brand(row.brand)


def initial_markers(
    color: str | None,
    class_name: str | None,
    brand: str | None,
    classification_status: str | None,
) -> dict[str, str]:
    """Resolution markers a freshly imported AI 24/7 bag starts with."""

    return {
        "color_resolution": (
            METHOD_UNRESOLVED if event_color_key(color, class_name) == UNKNOWN else ""
        ),
        "brand_resolution": (
            METHOD_UNRESOLVED if _is_unknown_brand(brand, classification_status) else ""
        ),
    }


def unknown_color_q() -> Q:
    """SQL twin of ``event_color_key(...) == "unknown"`` for bounded reads."""

    def unknown(field_name: str) -> Q:
        return Q(**{f"{field_name}__iexact": UNKNOWN}) | Q(
            **{f"{field_name}__istartswith": f"{UNKNOWN}_"}
        )

    return unknown("color") | (
        (Q(color__isnull=True) | Q(color="")) & unknown("class_name")
    )


# ---------------------------------------------------------------------------
# Database passes
# ---------------------------------------------------------------------------

_ROW_FIELDS = (
    "id",
    "camera",
    "upstream_event_id",
    "occurred_at",
    "color",
    "class_name",
    "brand",
    "classification_status",
    "verification_votes",
    "resolved_color",
    "color_resolution",
    "resolved_brand",
    "brand_resolution",
    "resolution_note",
)
_WRITE_FIELDS = [
    "resolved_color",
    "color_resolution",
    "resolved_brand",
    "brand_resolution",
    "resolution_note",
]


def _journal(camera: str):
    """Continuous AI 24/7 bags of one camera: the only resolution scope."""

    return AlwaysOnImportedEvent.objects.filter(
        camera=camera,
        analytics_scope=ANALYTICS_SCOPE_AI247,
        applied_to_analytics=True,
    )


def _votes(row: AlwaysOnImportedEvent, key: str) -> dict[str, int]:
    raw = (row.verification_votes or {}).get(key)
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): count
        for name, count in raw.items()
        if isinstance(count, int) and not isinstance(count, bool) and count > 0
    }


def _decided_color(
    row: AlwaysOnImportedEvent, decision: Resolution | None
) -> str | None:
    """Colour of a bag after this pass (a new decision wins over the stored)."""

    if decision is not None:
        return decision.value
    if row.color_resolution in RESOLVED_METHODS and row.resolved_color:
        return row.resolved_color
    return _known_color(row)


def _terminal_days(camera: str, days: set[date]) -> set[date]:
    if not days:
        return set()
    return set(
        AlwaysOnStockBatch.objects.filter(
            camera=camera,
            business_day__in=days,
            status__in=TERMINAL_BATCH_STATUSES,
        ).values_list("business_day", flat=True)
    )


def _evaluate(
    rows: list[AlwaysOnImportedEvent],
    *,
    query_start: datetime,
    query_end: datetime | None,
    write_from: datetime,
    write_to: datetime | None,
) -> int:
    """Run the pure resolver over loaded rows and persist changed decisions."""

    if not rows:
        return 0
    open_start = rows[0].occurred_at - query_start <= MAX_GAP
    open_end = query_end is not None and query_end - rows[-1].occurred_at <= MAX_GAP
    options = {"open_start": open_start, "open_end": open_end}
    colors = resolve_sequence(
        [
            Bag(
                row.occurred_at,
                _known_color(row),
                _is_color_candidate(row),
                _votes(row, "color"),
                unknown=row.color_resolution == METHOD_MANUAL,
            )
            for row in rows
        ],
        **options,
    )
    brands = resolve_sequence(
        [
            Bag(
                row.occurred_at,
                _known_brand(row),
                _is_brand_candidate(row),
                _votes(row, "brand"),
                unknown=row.brand_resolution == METHOD_MANUAL,
            )
            for row in rows
        ],
        **options,
    )
    for index, row in enumerate(rows):
        # A white bag lies face down (cv-service ``white_reverse``): its brand
        # is never visible, so bags beyond the white ones must not lend theirs.
        if brands[index] is not None and _decided_color(row, colors[index]) == WHITE:
            brands[index] = Resolution(None, METHOD_UNRESOLVED, WHITE_BRAND_NOTE)
    decided = [
        index
        for index, row in enumerate(rows)
        if (colors[index] is not None or brands[index] is not None)
        and row.occurred_at >= write_from
        and (write_to is None or row.occurred_at < write_to)
    ]
    frozen = _terminal_days(
        rows[0].camera,
        {business_day_for(rows[index].occurred_at) for index in decided},
    )
    changed: list[AlwaysOnImportedEvent] = []
    for index in decided:
        row = rows[index]
        if business_day_for(row.occurred_at) in frozen:
            continue  # posted shifts change only through an audited command
        note = dict(row.resolution_note or {})
        dirty = False
        for decision, value_field, method_field, note_key in (
            (colors[index], "resolved_color", "color_resolution", "color"),
            (brands[index], "resolved_brand", "brand_resolution", "brand"),
        ):
            if decision is None:
                continue
            value = decision.value or ""
            # Stored truncated: compare the stored form or every pass rewrites.
            note_text = decision.note[:300]
            if (
                getattr(row, value_field) != value
                or getattr(row, method_field) != decision.method
                or note.get(note_key) != note_text
            ):
                setattr(row, value_field, value)
                setattr(row, method_field, decision.method)
                note[note_key] = note_text
                dirty = True
        if dirty:
            row.resolution_note = note
            changed.append(row)
    if changed:
        AlwaysOnImportedEvent.objects.bulk_update(
            changed, _WRITE_FIELDS, batch_size=500
        )
    return len(changed)


def _ordered(queryset):
    return queryset.order_by("occurred_at", "upstream_event_id").only(*_ROW_FIELDS)


def resolve_after_import(camera: str, since: datetime) -> int:
    """Re-decide recent unknown bags after an imported page.

    Runs inside the page transaction (the camera cursor lock is held). A new
    page can settle an earlier bag whose "after" neighbour was still missing,
    so the window reaches ``IMPORT_LOOKBACK`` before the page's first bag.
    """

    window_start = since - IMPORT_LOOKBACK
    journal = _journal(camera)
    if not journal.filter(
        Q(color_resolution__in=AUTO_METHODS) | Q(brand_resolution__in=AUTO_METHODS),
        occurred_at__gte=window_start,
    ).exists():
        return 0
    query_start = window_start - WINDOW_CONTEXT
    rows = list(_ordered(journal.filter(occurred_at__gte=query_start)))
    return _evaluate(
        rows,
        query_start=query_start,
        query_end=None,
        write_from=window_start,
        write_to=None,
    )


def business_day_window(business_day: date) -> tuple[datetime, datetime]:
    """[previous 19:00, this 19:00) — the production shift of ``business_day``."""

    return scheduled_for(business_day - timedelta(days=1)), scheduled_for(business_day)


def resolve_business_day(camera: str, business_day: date) -> int:
    """Authoritative pass over a whole shift, run right before it is posted."""

    start, end = business_day_window(business_day)
    journal = _journal(camera)
    if not journal.filter(
        Q(color_resolution__in=AUTO_METHODS)
        | Q(brand_resolution__in=AUTO_METHODS)
        # Rows written by a rolled-back image carry no marker yet.
        | (Q(color_resolution="") & unknown_color_q())
        | (Q(brand_resolution="") & Q(brand__iexact=UNKNOWN)),
        occurred_at__gte=start,
        occurred_at__lt=end,
    ).exists():
        return 0
    query_start, query_end = start - WINDOW_CONTEXT, end + WINDOW_CONTEXT
    rows = list(
        _ordered(
            journal.filter(occurred_at__gte=query_start, occurred_at__lt=query_end)
        )
    )
    return _evaluate(
        rows,
        query_start=query_start,
        query_end=query_end,
        write_from=start,
        write_to=end,
    )


def awaits_following_bags(
    camera: str, business_day: date, caught_up_at: datetime | None
) -> bool:
    """Whether a bag near the 19:00 cutoff may still get its "after" neighbour.

    The pass before posting freezes decisions for good. A bag without an
    answer in the last ``max_gap`` of the shift can borrow from the first bags
    of the next one, so posting waits until the journal is caught up that far.
    """

    cutoff = scheduled_for(business_day)
    if caught_up_at is not None and caught_up_at >= cutoff + MAX_GAP:
        return False
    return (
        _journal(camera)
        .filter(
            Q(color_resolution__in=AUTO_METHODS) | Q(brand_resolution__in=AUTO_METHODS),
            occurred_at__gte=cutoff - MAX_GAP,
            occurred_at__lt=cutoff,
        )
        .exists()
    )


def pending_unknown_events(camera: str, business_day: date, limit: int):
    """Oldest bags of a shift still waiting for a colour, locked for update."""

    start, end = business_day_window(business_day)
    return list(
        AlwaysOnImportedEvent.objects.select_for_update()
        .filter(
            unknown_color_q(),
            camera=camera,
            analytics_scope=ANALYTICS_SCOPE_AI247,
            applied_to_production=True,
            occurred_at__gte=start,
            occurred_at__lt=end,
            color_resolution__in=("", METHOD_UNRESOLVED),
        )
        .order_by("occurred_at", "upstream_event_id")[:limit]
    )


# ---------------------------------------------------------------------------
# Transfers: resolved bags leave ``unknown`` and join their colour
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Transfers:
    """Net bag movement per bucket and how the incoming bags were resolved."""

    delta: dict[str, int]
    inferred: dict[str, dict[str, int]]


def _transfers(rows, available: Mapping[str, int]) -> Transfers:
    """Never move more bags than a bucket holds.

    A bucket can hold fewer bags than resolved events when a historical
    correction subtracted part of it or, for analytics, when the day was
    split by an archive. The bags beyond it are not moved twice.
    Manual assignments take the capacity first, so a later automatic
    decision can never push an audited assignment out; within one method
    the given order (newest first) decides.
    """

    remaining = {key: max(0, int(value)) for key, value in available.items()}
    delta: dict[str, int] = defaultdict(int)
    inferred: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for source, target, method in sorted(
        rows, key=lambda row: _METHOD_PRIORITY.get(row[2], len(_METHOD_PRIORITY))
    ):
        if not target or target == source or remaining.get(source, 0) <= 0:
            continue
        remaining[source] -= 1
        delta[source] -= 1
        delta[target] += 1
        inferred[target][method] += 1
    return Transfers(
        {key: value for key, value in delta.items() if value},
        {key: dict(sorted(value.items())) for key, value in inferred.items()},
    )


def business_day_transfers(
    camera: str,
    business_day: date,
    available: Mapping[str, int],
) -> Transfers:
    """Colour transfers for one production shift (stock posting source)."""

    start, end = business_day_window(business_day)
    rows = (
        AlwaysOnImportedEvent.objects.filter(
            camera=camera,
            analytics_scope=ANALYTICS_SCOPE_AI247,
            applied_to_production=True,
            occurred_at__gte=start,
            occurred_at__lt=end,
            color_resolution__in=RESOLVED_METHODS,
        )
        .exclude(resolved_color="")
        .order_by("-occurred_at", "-upstream_event_id")
        .values_list("color", "class_name", "resolved_color", "color_resolution")
    )
    return _transfers(
        (
            (camera_color_key(color, class_name), resolved, method)
            for color, class_name, resolved, method in rows
        ),
        available,
    )


def overlay_daily_rows(rows) -> dict[tuple[str, date], dict[str, dict[str, int]]]:
    """Show resolved colours in active AI 24/7 daily analytics rows.

    The stored daily ledger keeps the camera's answers; this replaces the
    in-memory ``model_per_color`` of the given (unsaved, read-only) rows with
    one grouped read. Returns what was inferred per (camera, day):
    ``{colour: {method: n}}``.
    """

    rows = [row for row in rows if row.model_total > 0]
    if not rows:
        return {}
    days = [row.day for row in rows]
    start, end = local_day_window(min(days), max(days))
    by_key = {(row.camera, row.day): row for row in rows}
    cameras = {row.camera for row in rows}
    # Archiving zeroed the live day: the bags imported before that moment
    # left this row for the archive snapshot.
    archived_before: dict[tuple[str, date], datetime] = {}
    for camera, created_at in AlwaysOnCountArchive.objects.filter(
        camera__in=cameras, created_at__gte=start, created_at__lt=end
    ).values_list("camera", "created_at"):
        key = (camera, local_date(created_at))
        archived_before[key] = max(archived_before.get(key, created_at), created_at)
    events = (
        AlwaysOnImportedEvent.objects.filter(
            ~Q(resolved_color=""),
            color_resolution__in=RESOLVED_METHODS,
            camera__in=cameras,
            analytics_scope=ANALYTICS_SCOPE_AI247,
            applied_to_analytics=True,
            occurred_at__gte=start,
            occurred_at__lt=end,
        )
        .order_by("camera", "-occurred_at", "-upstream_event_id")
        .values_list(
            "camera",
            "occurred_at",
            "imported_at",
            "color",
            "class_name",
            "resolved_color",
            "color_resolution",
        )
    )
    color_moves: dict[tuple[str, date], list] = defaultdict(list)
    for (
        camera,
        occurred_at,
        imported_at,
        color,
        class_name,
        resolved_color,
        color_method,
    ) in events:
        key = (camera, local_date(occurred_at))
        if key not in by_key:
            continue
        if key in archived_before and imported_at < archived_before[key]:
            continue
        source = event_color_key(color, class_name)
        if source:
            color_moves[key].append((source, resolved_color, color_method))

    inferred: dict[tuple[str, date], dict[str, dict[str, int]]] = {}
    for key, moves in color_moves.items():
        row = by_key[key]
        colors = {
            str(name): int(value)
            for name, value in (row.model_per_color or {}).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        transfer = _transfers(moves, colors)
        row.model_per_color = _moved(colors, transfer.delta)
        inferred[key] = transfer.inferred
    return inferred


def _moved(counts: dict[str, int], delta: Mapping[str, int]) -> dict[str, int]:
    result = dict(counts)
    for key, value in delta.items():
        result[key] = result.get(key, 0) + value
    return {key: value for key, value in result.items() if value > 0}


def merge_inferred(
    *items: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, int]]:
    """Sum ``{colour: {method: n}}`` maps (period totals of several days)."""

    result: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for item in items:
        for name, methods in (item or {}).items():
            for method, count in methods.items():
                result[name][method] += int(count)
    return {name: dict(sorted(methods.items())) for name, methods in result.items()}


def with_inferred(items: list[dict], key: str, inferred_parts) -> list[dict]:
    """Mark how many bags of each colour/brand item CRM resolved and how.

    ``items`` are analytics breakdown rows (``{key: name, total, percent}``);
    ``inferred_parts`` are ``{name: {method: n}}`` maps to sum. The rows get
    ``inferred`` only where something was resolved, so the screen can show a
    subtle «по соседям»/«по голосам»/«вручную» marker.
    """

    merged = merge_inferred(*[part for part in inferred_parts if part])
    for item in items:
        methods = merged.get(item[key])
        if methods:
            item["inferred"] = methods
    return items


# ---------------------------------------------------------------------------
# Selected-day display: recolour ``unknown`` runs by their bags' decisions
# ---------------------------------------------------------------------------


def resolved_day_runs(
    camera: str,
    rows: Sequence,
    payloads: list[dict],
    *,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Return display runs where resolved bags carry their colour and marker.

    ``rows``/``payloads`` are the day's ``AlwaysOnProductionRun`` rows and
    their API payloads in the same order. An ``unknown`` run is split into
    consecutive segments by its bags' decisions (``inferred`` = how, and
    ``segment`` when it was split); the run ledger itself is never changed.
    A run whose bags cannot be matched exactly to the journal stays as is.
    """

    targets = [
        index
        for index, payload in enumerate(payloads)
        if payload.get("color") in PENDING_COLORS
        and not payload.get("is_partial_for_day")
        and not payload.get("is_approximate")
    ]
    events = []
    if targets:
        events = list(
            AlwaysOnImportedEvent.objects.filter(
                unknown_color_q(),
                camera=camera,
                analytics_scope=ANALYTICS_SCOPE_AI247,
                applied_to_production=True,
                occurred_at__gte=start,
                occurred_at__lt=end,
            )
            .order_by("occurred_at", "upstream_event_id")
            .values_list("occurred_at", "resolved_color", "color_resolution")
        )
    display: list[dict] = []
    target_set = set(targets)
    for index, payload in enumerate(payloads):
        row = rows[index]
        matched = (
            [
                event
                for event in events
                if row.started_at <= event[0] <= row.last_counted_at
            ]
            if index in target_set
            else []
        )
        if index not in target_set or len(matched) != row.model_bags:
            display.append(payload)
            continue
        segments: list[list] = []
        for occurred_at, resolved, method in matched:
            resolved_here = method in RESOLVED_METHODS and bool(resolved)
            color = resolved if resolved_here else payload["color"]
            if segments and segments[-1][0] == color:
                segments[-1][1].append((occurred_at, method if resolved_here else None))
            else:
                segments.append(
                    [color, [(occurred_at, method if resolved_here else None)]]
                )
        if len(segments) == 1 and segments[0][0] == payload["color"]:
            display.append(payload)
            continue
        for position, (color, bags) in enumerate(segments):
            item = dict(payload)
            methods = Counter(method for _at, method in bags if method)
            item.update(color=color, model_bags=len(bags))
            if methods:
                item["inferred"] = dict(sorted(methods.items()))
                item["source_color"] = payload["color"]
            if len(segments) > 1:
                item["segment"] = position
                item["started_at"] = (
                    _iso(bags[0][0]) if position else payload["started_at"]
                )
                if position < len(segments) - 1:
                    item["last_counted_at"] = _iso(bags[-1][0])
                    item["ended_at"] = _iso(bags[-1][0])
                    item["status"] = "closed"
            display.append(item)
    return display
