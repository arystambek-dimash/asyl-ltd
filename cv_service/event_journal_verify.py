from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from pathlib import Path

SCHEMA_VERSION = 1
EVENT_COLUMNS = (
    ("id", "INTEGER", 0, 1),
    ("event_key", "TEXT", 1, 0),
    ("created_at", "TEXT", 1, 0),
    ("cam", "TEXT", 1, 0),
    ("source", "TEXT", 1, 0),
    ("mode", "TEXT", 1, 0),
    ("generation", "INTEGER", 1, 0),
    ("frame", "INTEGER", 1, 0),
    ("track_id", "INTEGER", 1, 0),
    ("class_id", "INTEGER", 1, 0),
    ("class_name", "TEXT", 1, 0),
    ("confidence", "REAL", 1, 0),
    ("direction", "TEXT", 1, 0),
    ("point_x", "REAL", 1, 0),
    ("point_y", "REAL", 1, 0),
    ("weight_kg", "REAL", 1, 0),
    ("total_after", "INTEGER", 1, 0),
    ("total_weight_after", "REAL", 1, 0),
)
META_COLUMNS = (
    ("key", "TEXT", 0, 1),
    ("value", "TEXT", 1, 0),
)


class JournalVerificationError(ValueError):
    """The file is not an exact supported canonical count-event journal."""


def _table_columns(connection: sqlite3.Connection, table: str) -> tuple:
    return tuple(
        (str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5]))
        for row in connection.execute(f'PRAGMA table_info("{table}")')
    )


def _index_columns(connection: sqlite3.Connection, index: str) -> tuple[str, ...]:
    quoted = index.replace('"', '""')
    return tuple(
        str(row[2])
        for row in connection.execute(f'PRAGMA index_info("{quoted}")')
    )


def validate_existing_connection(connection: sqlite3.Connection) -> str | None:
    """Validate without mutation; return UUID or ``None`` for an empty v0 DB."""

    quick_check = connection.execute("PRAGMA quick_check").fetchone()
    if quick_check is None or str(quick_check[0]).lower() != "ok":
        raise JournalVerificationError("count-event journal failed quick_check")
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    objects = list(connection.execute(
        """
        SELECT type, name, sql FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ))
    if not objects:
        if version != 0:
            raise JournalVerificationError(
                f"empty count-event DB has unsupported schema version {version}"
            )
        return None

    tables = {str(row[1]) for row in objects if row[0] == "table"}
    if tables != {"count_event_meta", "count_events"}:
        raise JournalVerificationError(
            "count-event DB contains unknown or partial tables"
        )
    if any(row[0] in {"trigger", "view"} for row in objects):
        raise JournalVerificationError(
            "count-event DB contains unsupported triggers or views"
        )
    indexes = {str(row[1]) for row in objects if row[0] == "index"}
    if indexes != {"count_events_cam_id"}:
        raise JournalVerificationError(
            "count-event DB contains unknown or partial indexes"
        )
    if version != SCHEMA_VERSION:
        raise JournalVerificationError(
            f"count-event DB schema version {version} is not supported"
        )

    if _table_columns(connection, "count_event_meta") != META_COLUMNS:
        raise JournalVerificationError("count_event_meta schema is incompatible")
    if _table_columns(connection, "count_events") != EVENT_COLUMNS:
        raise JournalVerificationError("count_events schema is incompatible")
    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='count_events'"
    ).fetchone()
    if table_sql_row is None or "AUTOINCREMENT" not in str(table_sql_row[0]).upper():
        raise JournalVerificationError("count_events id is not AUTOINCREMENT")

    user_indexes = {
        str(row[1])
        for row in connection.execute("PRAGMA index_list('count_events')")
        if not str(row[1]).startswith("sqlite_")
    }
    if user_indexes != {"count_events_cam_id"}:
        raise JournalVerificationError("count_events indexes are incompatible")
    cam_index = next(
        row
        for row in connection.execute("PRAGMA index_list('count_events')")
        if str(row[1]) == "count_events_cam_id"
    )
    if int(cam_index[2]) != 0 or _index_columns(
        connection, "count_events_cam_id"
    ) != ("cam", "id"):
        raise JournalVerificationError("count_events_cam_id is incompatible")
    unique_event_key = any(
        int(row[2]) == 1 and _index_columns(connection, str(row[1])) == ("event_key",)
        for row in connection.execute("PRAGMA index_list('count_events')")
    )
    if not unique_event_key:
        raise JournalVerificationError("count_events.event_key is not unique")

    metadata = list(connection.execute(
        "SELECT key, value FROM count_event_meta ORDER BY key"
    ))
    if len(metadata) != 1 or metadata[0][0] != "journal_id":
        raise JournalVerificationError("count-event journal identity is missing")
    journal_id = str(metadata[0][1])
    try:
        parsed = uuid.UUID(journal_id)
    except (AttributeError, ValueError) as exc:
        raise JournalVerificationError(
            "count-event journal identity is not a UUID"
        ) from exc
    if str(parsed) != journal_id:
        raise JournalVerificationError(
            "count-event journal identity is not a canonical UUID"
        )
    return journal_id


def verify_existing_path(path: Path) -> str | None:
    resolved = Path(path).resolve()
    uri = f"{resolved.as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
    except sqlite3.Error as exc:
        raise JournalVerificationError(
            f"cannot open existing count-event DB read-only: {exc}"
        ) from exc
    try:
        return validate_existing_connection(connection)
    except sqlite3.Error as exc:
        raise JournalVerificationError(
            f"cannot verify existing count-event DB: {exc}"
        ) from exc
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        journal_id = verify_existing_path(args.path)
    except JournalVerificationError as exc:
        parser.exit(2, f"count-event journal verification failed: {exc}\n")
    print(json.dumps({"compatible": True, "journal_id": journal_id}))


if __name__ == "__main__":
    main()
