"""Pure neighbour/vote resolver for bags the camera left without a colour.

Sequences are written as space-separated tokens: a colour letter is a bag the
camera classified, ``?`` is a bag to resolve and ``.`` is a counted bag that
carries no colour information at all (legacy weight-only event). Each bag is
10 seconds after the previous one unless a test passes explicit offsets.
"""

from datetime import datetime, timedelta, timezone

from apps.cameras.color_resolution import Bag, resolve_sequence
from apps.cameras.event_protocol import compact_votes

T0 = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
NAMES = {"R": "red", "B": "blue", "G": "green"}


def _bags(pattern: str, *, offsets=None, votes=None) -> list[Bag]:
    tokens = pattern.split()
    offsets = offsets or [index * 10 for index in range(len(tokens))]
    votes = votes or {}
    return [
        Bag(
            at=T0 + timedelta(seconds=offsets[index]),
            value=NAMES.get(token),
            candidate=token == "?",
            votes=votes.get(index, {}),
        )
        for index, token in enumerate(tokens)
    ]


def _values(pattern: str, *, offsets=None, votes=None, **options) -> list:
    """(value, method) of every ``?`` bag in the pattern."""

    decisions = resolve_sequence(
        _bags(pattern, offsets=offsets, votes=votes), **options
    )
    return [
        None if decision is None else (decision.value, decision.method)
        for token, decision in zip(pattern.split(), decisions)
        if token == "?"
    ]


def test_unknown_inside_a_run_takes_the_agreeing_neighbours():
    assert _values("R R ? R") == [("red", "neighbors")]


def test_consecutive_unknowns_inside_a_homogeneous_run_all_resolve():
    assert _values("R R ? ? ? R R") == [("red", "neighbors")] * 3


def test_bags_without_colour_information_are_skipped_but_keep_continuity():
    assert _values("R . ? . R") == [("red", "neighbors")]


def test_disagreeing_neighbours_use_partial_votes_for_one_of_them():
    assert _values("R R ? B B", votes={2: {"blue": 1}}) == [("blue", "votes")]


def test_votes_for_a_third_colour_do_not_decide_a_run_boundary():
    assert _values("R R ? B B", votes={2: {"green": 1}}) == [(None, "unresolved")]


def test_tied_votes_do_not_decide():
    assert _values("R ? B", votes={1: {"red": 1, "blue": 1}}) == [(None, "unresolved")]


def test_equidistant_disagreeing_neighbours_without_votes_stay_unresolved():
    assert _values("R ? B") == [(None, "unresolved")]


def test_clearly_nearer_neighbour_wins_at_a_run_boundary():
    # 3 s after red, 30 s before blue: 3/30 <= 1/3.
    assert _values("R R ? B B", offsets=[0, 10, 13, 43, 53]) == [("red", "neighbors")]


def test_nearer_neighbour_must_be_clearly_nearer():
    # 10 s vs 20 s is a ratio of 1/2 — too close to call.
    assert _values("R ? B", offsets=[0, 10, 30]) == [(None, "unresolved")]


def test_start_of_data_resolves_from_a_homogeneous_following_run():
    assert _values("? ? R R R") == [("red", "neighbors")] * 2


def test_one_sided_neighbour_needs_two_consecutive_same_colours():
    assert _values("? R B") == [(None, "unresolved")]
    assert _values("B R ?") == [(None, "unresolved")]
    assert _values("R R ?") == [("red", "neighbors")]


def test_one_sided_neighbour_is_rejected_when_votes_point_elsewhere():
    assert _values("R R ?", votes={2: {"blue": 1}}) == [(None, "unresolved")]
    assert _values("R R ?", votes={2: {"red": 1}}) == [("red", "neighbors")]


def test_long_pause_is_a_run_boundary():
    # 10 minutes of silence on both sides: nothing continuous to borrow from.
    assert _values("R R ? R R", offsets=[0, 10, 610, 1210, 1220]) == [
        (None, "unresolved")
    ]


def test_long_pause_on_one_side_leaves_a_one_sided_decision():
    assert _values("R R ? B B", offsets=[0, 10, 20, 900, 910]) == [("red", "neighbors")]


def test_max_gap_is_configurable():
    pattern, offsets = "R R ? R", [0, 10, 70, 80]
    assert _values(pattern, offsets=offsets) == [("red", "neighbors")]
    # A 60 s pause now breaks the preceding run; one following red bag alone
    # is not a homogeneous run.
    assert _values(pattern, offsets=offsets, max_gap=timedelta(seconds=30)) == [
        (None, "unresolved")
    ]


def test_no_neighbours_at_all_is_unresolved():
    assert _values("? ?") == [(None, "unresolved")] * 2
    assert _values(". ? .") == [(None, "unresolved")]


def test_truncated_edges_are_inconclusive_not_unresolved():
    # The loaded window may cut through a continuous stretch: do not guess.
    bags = _bags("? R R")
    assert resolve_sequence(bags, open_start=True)[0] is None
    bags = _bags("R R ? ?")
    assert resolve_sequence(bags, open_end=True)[2:] == [None, None]
    # A closed edge still allows the one-sided rule.
    assert resolve_sequence(bags, open_end=False)[2].value == "red"


def test_non_candidates_have_no_decision():
    decisions = resolve_sequence(_bags("R ? R"))
    assert decisions[0] is None and decisions[2] is None


def test_resolution_is_deterministic_and_idempotent():
    bags = _bags("R R ? ? B B ? G G", offsets=[0, 10, 12, 50, 55, 60, 65, 66, 70])
    first = resolve_sequence(bags)
    assert resolve_sequence(bags) == first
    assert resolve_sequence(list(bags)) == first


def test_a_later_neighbour_changes_an_earlier_one_sided_decision():
    head = _bags("R R ?")
    assert resolve_sequence(head)[2].value == "red"
    extended = _bags("R R ? B B", offsets=[0, 10, 20, 22, 30])
    # blue arrived 2 s later versus red 10 s before: blue is clearly nearer.
    assert resolve_sequence(extended)[2].value == "blue"


def test_late_neighbour_resolves_an_earlier_unresolved_bag():
    assert _values("? R") == [(None, "unresolved")]
    assert _values("? R R") == [("red", "neighbors")]


def test_notes_explain_the_evidence():
    decision = resolve_sequence(_bags("R ? B", votes={1: {"blue": 2, "red": 1}}))[1]
    assert decision.value == "blue"
    assert "red" in decision.note and "blue" in decision.note


def test_compact_votes_keep_only_counts_from_the_verification_object():
    verification = {
        "version": 1,
        "reason": "track_ended",
        "minimum_votes": 2,
        "expected_lines": ["count", "before", "after"],
        "observed_lines": ["before", "count"],
        "distinct_frames": 2,
        "samples": [
            {
                "frame": 1,
                "line_ids": ["before"],
                "bbox": [1, 2, 3, 4],
                "prediction": {
                    "color": "Red_50",
                    "color_confidence": 0.9,
                    "brand": "Korol",
                    "brand_confidence": 0.8,
                    "sku": "red_korol",
                    "classification_status": "recognized",
                },
            },
            {
                "frame": 2,
                "line_ids": ["count"],
                "bbox": [1, 2, 3, 4],
                "prediction": {
                    "color": "Blue_50",
                    "color_confidence": 0.7,
                    "brand": "unknown",
                    "brand_confidence": 0.0,
                    "sku": "blue_unknown",
                    "classification_status": "needs_review",
                },
            },
            {
                "frame": 3,
                "line_ids": ["after"],
                "prediction": None,
                "error": "crop_too_small",
            },
        ],
    }
    assert compact_votes(verification) == {
        "color": {"blue": 1, "red": 1},
        "brand": {"korol": 1},
        "frames": 2,
        "reason": "track_ended",
    }


def test_compact_votes_ignore_missing_or_malformed_evidence():
    assert compact_votes(None) == {}
    assert compact_votes("x") == {}
    assert compact_votes({"samples": "nope"}) == {}
    assert compact_votes(
        {"samples": [{"prediction": {"color": 5}}], "reason": "r" * 80}
    ) == {"frames": 1}
    assert compact_votes(
        {"samples": [{"prediction": {"color": "unknown", "brand": "unknown"}}]}
    ) == {"frames": 1}


# ---------------------------------------------------------------------------
# A long stretch without an answer is a camera problem, not a random miss
# ---------------------------------------------------------------------------


def test_a_long_streak_of_unknown_bags_after_a_run_stays_unresolved():
    # 180 bags (30 minutes) without a colour right after two red bags: the
    # camera has stopped telling colours apart (new product, lens, light).
    pattern = "R R " + " ".join(["?"] * 180)
    assert set(_values(pattern)) == {(None, "unresolved")}


def test_a_long_streak_between_two_agreeing_runs_stays_unresolved():
    pattern = "R R " + " ".join(["?"] * 180) + " R R"
    decisions = resolve_sequence(_bags(pattern))
    assert {decision.value for decision in decisions if decision} == {None}
    assert "подряд 180" in decisions[2].note


def test_max_streak_is_configurable_and_counts_only_bags_without_an_answer():
    assert _values("R R ? ? ? ? ? R R") == [("red", "neighbors")] * 5
    assert _values("R R ? ? ? ? ? ? R R") == [(None, "unresolved")] * 6
    assert _values("R R ? ? ? R R", max_streak=2) == [(None, "unresolved")] * 3
    # Legacy bags without any colour field keep continuity but are not misses.
    assert _values("R R ? . . . . . ? R R") == [("red", "neighbors")] * 2


def test_bags_resolved_by_hand_count_in_the_streak_but_lend_no_colour():
    bags = _bags("R R ? ? ? ? ? ? R R")
    # Two of the six unknown bags were assigned by an operator: they are no
    # evidence for the others, and the camera still missed six in a row.
    for index in (3, 4):
        bags[index] = Bag(at=bags[index].at, value=None, unknown=True)
    decisions = resolve_sequence(bags)
    assert [decisions[index].value for index in (2, 5, 6, 7)] == [None] * 4
    assert decisions[3] is None and decisions[4] is None


def test_the_neighbour_a_bag_borrows_from_must_itself_be_close():
    # Pauses of 170 s keep the stretch continuous, but the middle bag is
    # 340 s from both red runs: too far to borrow a colour from either.
    offsets = [0, 10, 180, 350, 520, 690, 700]
    assert _values("R R ? ? ? R R", offsets=offsets) == [
        ("red", "neighbors"),
        (None, "unresolved"),
        ("red", "neighbors"),
    ]


def test_a_long_streak_is_unresolved_even_when_the_window_is_cut():
    bags = _bags(" ".join(["?"] * 8) + " R R")
    decisions = resolve_sequence(bags, open_start=True)
    assert {decision.value for decision in decisions[:8]} == {None}
    assert {decision.method for decision in decisions[:8]} == {"unresolved"}
