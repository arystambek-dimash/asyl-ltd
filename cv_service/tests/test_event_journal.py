from __future__ import annotations

import sqlite3
import uuid
from dataclasses import replace

import pytest

from cv_service.event_journal import CountEvent, CountEventJournal


def event(
    *,
    cam: str = "cam3",
    total_after: int = 2_292,
    frame: int = 101,
) -> CountEvent:
    return CountEvent(
        created_at="2026-08-25T08:30:00.000+00:00",
        cam=cam,
        source="sub",
        mode="always_on",
        generation=7,
        frame=frame,
        track_id=19,
        class_id=0,
        class_name="Red_50",
        confidence=0.93,
        direction="positive",
        point_x=320.5,
        point_y=241.25,
        weight_kg=50.0,
        total_after=total_after,
        total_weight_after=114_600.0,
    )


def test_event_and_journal_identity_survive_reopen(tmp_path):
    path = tmp_path / "state" / "count-events.sqlite3"
    journal = CountEventJournal(path)
    journal_id = journal.journal_id
    assert uuid.UUID(journal_id).version == 4
    assert journal.append(event()) == 1
    journal.close()

    reopened = CountEventJournal(path)
    try:
        page = reopened.page(after_id=0, limit=500, cam="cam3")
        assert page["journal_id"] == journal_id
        assert page["next_after_id"] == 1
        assert page["has_more"] is False
        assert page["events"] == [{
            "id": 1,
            "created_at": "2026-08-25T08:30:00.000+00:00",
            "cam": "cam3",
            "source": "sub",
            "mode": "always_on",
            "generation": 7,
            "frame": 101,
            "track_id": 19,
            "class_id": 0,
            "class_name": "Red_50",
            "confidence": 0.93,
            "direction": "positive",
            "point_x": 320.5,
            "point_y": 241.25,
            "weight_kg": 50.0,
            "total_after": 2_292,
            "total_weight_after": 114_600.0,
        }]
    finally:
        reopened.close()


def test_pages_use_global_ids_and_camera_filter_does_not_rebase_cursor(tmp_path):
    journal = CountEventJournal(tmp_path / "count-events.sqlite3")
    try:
        assert journal.append(event(cam="cam2", frame=1)) == 1
        assert journal.append(event(cam="cam3", frame=2)) == 2
        assert journal.append(event(cam="cam2", frame=3)) == 3

        first = journal.page(after_id=0, limit=1, cam="cam2")
        assert [item["id"] for item in first["events"]] == [1]
        assert first["next_after_id"] == 1
        assert first["has_more"] is True

        second = journal.page(
            after_id=first["next_after_id"], limit=1, cam="cam2",
        )
        assert [item["id"] for item in second["events"]] == [3]
        assert second["next_after_id"] == 3
        assert second["has_more"] is False

        cam3 = journal.page(after_id=0, limit=500, cam="cam3")
        assert [item["id"] for item in cam3["events"]] == [2]

        empty = journal.page(after_id=99, limit=500, cam="cam3")
        assert empty["events"] == []
        assert empty["next_after_id"] == 99
        assert empty["has_more"] is False
    finally:
        journal.close()


@pytest.mark.parametrize(
    ("after_id", "limit", "cam"),
    [(-1, 500, None), (0, 0, None), (0, 501, None), (0, 500, "CAM3")],
)
def test_page_rejects_out_of_contract_parameters(tmp_path, after_id, limit, cam):
    journal = CountEventJournal(tmp_path / "count-events.sqlite3")
    try:
        with pytest.raises(ValueError):
            journal.page(after_id=after_id, limit=limit, cam=cam)
    finally:
        journal.close()


def test_recreated_database_has_a_new_journal_identity(tmp_path):
    first = CountEventJournal(tmp_path / "first.sqlite3")
    second = CountEventJournal(tmp_path / "second.sqlite3")
    try:
        assert first.journal_id != second.journal_id
    finally:
        first.close()
        second.close()


def test_begin_lock_failure_degrades_health_and_next_append_recovers(tmp_path):
    path = tmp_path / "count-events.sqlite3"
    journal = CountEventJournal(path)
    blocker = sqlite3.connect(path, isolation_level=None)
    try:
        journal._connection.execute("PRAGMA busy_timeout = 1")
        blocker.execute("BEGIN IMMEDIATE")

        with pytest.raises(RuntimeError, match="locked"):
            journal.append(event())

        assert journal.health()["available"] is False
        assert "locked" in journal.health()["error"]
        blocker.execute("ROLLBACK")

        assert journal.append(event()) == 1
        assert journal.health()["available"] is True
    finally:
        if blocker.in_transaction:
            blocker.execute("ROLLBACK")
        blocker.close()
        journal.close()


def test_same_crossing_key_is_exactly_once_and_returns_existing_id(tmp_path):
    journal = CountEventJournal(tmp_path / "count-events.sqlite3")
    crossing = event()
    try:
        assert journal.append(crossing) == 1
        assert journal.append(crossing) == 1
        assert [
            item["id"]
            for item in journal.page(after_id=0, limit=500)["events"]
        ] == [1]

        with pytest.raises(RuntimeError, match="replay changed"):
            journal.append(replace(crossing, total_after=2_293))
        assert journal.health()["available"] is False
    finally:
        journal.close()


def test_unknown_nonempty_sqlite_is_rejected_without_creating_tables(tmp_path):
    path = tmp_path / "unknown.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, payload TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="unknown or partial tables"):
        CountEventJournal(path)

    connection = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert tables == {"events"}
    finally:
        connection.close()


def test_higher_schema_version_is_rejected_without_overwrite(tmp_path):
    path = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 2")
    connection.close()

    with pytest.raises(RuntimeError, match="unsupported schema version 2"):
        CountEventJournal(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0] == 0
    finally:
        connection.close()
