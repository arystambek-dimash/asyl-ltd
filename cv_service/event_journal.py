from __future__ import annotations

import math
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .event_journal_verify import (
    SCHEMA_VERSION,
    validate_existing_connection,
    verify_existing_path,
)
from .settings import parse_camera

MAX_EVENT_PAGE_SIZE = 500
MAX_SQLITE_INTEGER = 9_223_372_036_854_775_807
EVENT_MODES = frozenset({"always_on", "session"})
EVENT_SOURCES = frozenset({"main", "sub"})
EVENT_DIRECTIONS = frozenset({"any", "up", "down", "positive", "negative"})


class JournalTransientError(RuntimeError):
    """A write may safely retry with the same CountEvent.event_key."""


class JournalIntegrityError(RuntimeError):
    """A retry cannot repair this event/schema/content violation."""


_TRANSIENT_SQLITE_CODES = frozenset({
    sqlite3.SQLITE_BUSY,
    sqlite3.SQLITE_LOCKED,
    sqlite3.SQLITE_IOERR,
    sqlite3.SQLITE_FULL,
    sqlite3.SQLITE_READONLY,
    sqlite3.SQLITE_PROTOCOL,
})


def _transient_sqlite_error(exc: sqlite3.Error) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    return isinstance(code, int) and (code & 0xFF) in _TRANSIENT_SQLITE_CODES


@dataclass(frozen=True)
class CountEvent:
    """One physical line crossing committed to the camera-PC journal."""

    created_at: str
    cam: str
    source: str
    mode: str
    generation: int
    frame: int
    track_id: int
    class_id: int
    class_name: str
    confidence: float
    direction: str
    point_x: float
    point_y: float
    weight_kg: float
    total_after: int
    total_weight_after: float
    event_key: str = field(default_factory=lambda: str(uuid.uuid4()), repr=False)


def utc_event_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def class_weight_kg(class_name: str) -> float:
    """Return the canonical ``Colour_Weight`` suffix without trusting input."""

    try:
        value = float(class_name.rsplit("_", 1)[1])
    except (IndexError, TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def _plain_int(value: object, *, name: str, minimum: int = 0) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > MAX_SQLITE_INTEGER
    ):
        raise ValueError(f"{name} must be an integer from {minimum}")
    return value


def _finite_float(value: object, *, name: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{name} must be a finite number")
    return result


def validate_event(event: CountEvent) -> CountEvent:
    camera = parse_camera(event.cam)
    if event.source not in EVENT_SOURCES:
        raise ValueError("event source must be main or sub")
    if event.mode not in EVENT_MODES:
        raise ValueError("event mode must be always_on or session")
    if event.direction not in EVENT_DIRECTIONS:
        raise ValueError("event direction is invalid")
    if not isinstance(event.class_name, str) or not 0 < len(event.class_name) <= 100:
        raise ValueError("event class_name is invalid")
    try:
        created_at = datetime.fromisoformat(event.created_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("event created_at must be ISO UTC") from exc
    if created_at.tzinfo is None or created_at.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError("event created_at must be ISO UTC")
    try:
        event_key = str(uuid.UUID(event.event_key))
    except (AttributeError, ValueError) as exc:
        raise ValueError("event event_key must be a canonical UUID") from exc
    if event_key != event.event_key:
        raise ValueError("event event_key must be a canonical UUID")

    return CountEvent(
        created_at=event.created_at,
        cam=camera,
        source=event.source,
        mode=event.mode,
        generation=_plain_int(event.generation, name="generation"),
        frame=_plain_int(event.frame, name="frame"),
        track_id=_plain_int(event.track_id, name="track_id"),
        class_id=_plain_int(event.class_id, name="class_id"),
        class_name=event.class_name,
        confidence=_finite_float(event.confidence, name="confidence"),
        direction=event.direction,
        point_x=_finite_float(event.point_x, name="point_x"),
        point_y=_finite_float(event.point_y, name="point_y"),
        weight_kg=_finite_float(event.weight_kg, name="weight_kg"),
        total_after=_plain_int(event.total_after, name="total_after", minimum=1),
        total_weight_after=_finite_float(
            event.total_weight_after,
            name="total_weight_after",
        ),
        event_key=event_key,
    )


class CountEventJournal:
    """Thread-safe, fsync-backed SQLite owner of the count-event stream."""

    _EVENT_COLUMNS = (
        "id",
        "created_at",
        "cam",
        "source",
        "mode",
        "generation",
        "frame",
        "track_id",
        "class_id",
        "class_name",
        "confidence",
        "direction",
        "point_x",
        "point_y",
        "weight_kg",
        "total_after",
        "total_weight_after",
    )

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._closed = False
        self._last_write_error = ""
        try:
            if str(self.path) != ":memory:":
                if self.path.exists():
                    verify_existing_path(self.path)
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                str(self.path),
                timeout=5.0,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute("PRAGMA trusted_schema = OFF")
            self.journal_id = self._initialize()
        except (OSError, sqlite3.Error, ValueError) as exc:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            raise RuntimeError(f"cannot initialize count-event journal: {exc}") from exc

    def _initialize(self) -> str:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                journal_id = validate_existing_connection(self._connection)
                if journal_id is None:
                    self._connection.execute(
                        """
                        CREATE TABLE count_event_meta (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL
                        )
                        """
                    )
                    self._connection.execute(
                        """
                        CREATE TABLE count_events (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            event_key TEXT NOT NULL UNIQUE,
                            created_at TEXT NOT NULL,
                            cam TEXT NOT NULL,
                            source TEXT NOT NULL,
                            mode TEXT NOT NULL,
                            generation INTEGER NOT NULL,
                            frame INTEGER NOT NULL,
                            track_id INTEGER NOT NULL,
                            class_id INTEGER NOT NULL,
                            class_name TEXT NOT NULL,
                            confidence REAL NOT NULL,
                            direction TEXT NOT NULL,
                            point_x REAL NOT NULL,
                            point_y REAL NOT NULL,
                            weight_kg REAL NOT NULL,
                            total_after INTEGER NOT NULL,
                            total_weight_after REAL NOT NULL
                        )
                        """
                    )
                    self._connection.execute(
                        """
                        CREATE INDEX count_events_cam_id
                        ON count_events(cam, id)
                        """
                    )
                    journal_id = str(uuid.uuid4())
                    self._connection.execute(
                        "INSERT INTO count_event_meta(key, value) VALUES ('journal_id', ?)",
                        (journal_id,),
                    )
                    self._connection.execute(
                        f"PRAGMA user_version = {SCHEMA_VERSION}"
                    )
                self._connection.execute("COMMIT")
                return journal_id
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def append(self, event: CountEvent) -> int:
        event = validate_event(event)
        values = asdict(event)
        with self._lock:
            self._assert_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    """
                    INSERT INTO count_events(
                        event_key, created_at, cam, source, mode, generation, frame,
                        track_id, class_id, class_name, confidence, direction,
                        point_x, point_y, weight_kg, total_after,
                        total_weight_after
                    ) VALUES (
                        :event_key, :created_at, :cam, :source, :mode, :generation, :frame,
                        :track_id, :class_id, :class_name, :confidence, :direction,
                        :point_x, :point_y, :weight_kg, :total_after,
                        :total_weight_after
                    )
                    ON CONFLICT(event_key) DO NOTHING
                    """,
                    values,
                )
                row = self._connection.execute(
                    "SELECT * FROM count_events WHERE event_key = ?",
                    (event.event_key,),
                ).fetchone()
                if row is None:
                    raise sqlite3.IntegrityError(
                        "event_key insert did not produce a row"
                    )
                stored = {
                    column: row[column]
                    for column in self._EVENT_COLUMNS
                    if column != "id"
                }
                expected = {
                    column: values[column]
                    for column in self._EVENT_COLUMNS
                    if column != "id"
                }
                if stored != expected:
                    raise sqlite3.IntegrityError(
                        "event_key replay changed event contents"
                    )
                self._connection.execute("COMMIT")
            except sqlite3.Error as exc:
                self._last_write_error = str(exc)
                if self._connection.in_transaction:
                    try:
                        self._connection.execute("ROLLBACK")
                    except sqlite3.Error:
                        # Preserve the original begin/commit/insert failure
                        # for health even if the connection is already lost.
                        pass
                error_type = (
                    JournalTransientError
                    if _transient_sqlite_error(exc)
                    else JournalIntegrityError
                )
                raise error_type(f"cannot append count event: {exc}") from exc
            self._last_write_error = ""
            return int(row["id"])

    def page(
        self,
        *,
        after_id: int,
        limit: int,
        cam: str | None = None,
    ) -> dict:
        after_id = _plain_int(after_id, name="after_id")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_EVENT_PAGE_SIZE
        ):
            raise ValueError(
                f"limit must be between 1 and {MAX_EVENT_PAGE_SIZE}"
            )
        camera = parse_camera(cam) if cam is not None else None
        with self._lock:
            self._assert_open()
            try:
                if camera is None:
                    rows = self._connection.execute(
                        "SELECT * FROM count_events WHERE id > ? ORDER BY id LIMIT ?",
                        (after_id, limit + 1),
                    ).fetchall()
                else:
                    rows = self._connection.execute(
                        """
                        SELECT * FROM count_events
                        WHERE id > ? AND cam = ?
                        ORDER BY id LIMIT ?
                        """,
                        (after_id, camera, limit + 1),
                    ).fetchall()
            except sqlite3.Error as exc:
                raise RuntimeError(f"cannot read count-event journal: {exc}") from exc
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        events = [{column: row[column] for column in self._EVENT_COLUMNS} for row in page_rows]
        return {
            "journal_id": self.journal_id,
            "events": events,
            "next_after_id": events[-1]["id"] if events else after_id,
            "has_more": has_more,
        }

    def health(self) -> dict:
        with self._lock:
            return {
                "available": not self._closed and not self._last_write_error,
                "journal_id": self.journal_id,
                "error": self._last_write_error or None,
            }

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("count-event journal is closed")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True
