"""Local, crash-safe queue. No Django, PostgreSQL, Redis or HTTP dependency."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Outbox:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "events.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                    body TEXT NOT NULL, photo BLOB, photo_done INTEGER NOT NULL DEFAULT 0,
                    ocr_done INTEGER NOT NULL DEFAULT 0, ready INTEGER NOT NULL DEFAULT 0,
                    acknowledged INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, occurred REAL NOT NULL, code TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, event):
        with self.connect() as db:
            db.execute("INSERT INTO events(id,body,created) VALUES(?,?,?)",
                       (event["id"], json.dumps(event), time.time()))

    def finish(self, key, part, *, updates=None, photo=None):
        if part not in {"photo", "ocr"}:
            raise ValueError("Unknown outbox part")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM events WHERE id=? AND ready=0", (key,)).fetchone()
            if not row:
                return
            body = json.loads(row["body"])
            body.update(updates or {})
            db.execute(f"UPDATE events SET body=?, photo=COALESCE(?,photo), {part}_done=1 WHERE id=?",
                       (json.dumps(body), photo, key))
            db.execute("UPDATE events SET ready=1 WHERE id=? AND photo_done=1 AND ocr_done=1", (key,))

    def recover(self):
        # Called only with the exclusive collector lock, before new workers.
        with self.connect() as db:
            db.execute("UPDATE events SET ready=1 WHERE ready=0")

    def next(self):
        with self.connect() as db:
            row = db.execute("SELECT * FROM events WHERE acknowledged=0 ORDER BY seq LIMIT 1").fetchone()
            if row is None or not row["ready"]:
                return None
            return {**json.loads(row["body"]), "photo": row["photo"]}

    def ack(self, key):
        with self.connect() as db:
            db.execute("UPDATE events SET acknowledged=1 WHERE id=? AND ready=1", (key,))

    def state(self, key, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute("INSERT INTO state(key,body) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body",
                           (key, json.dumps(value)))
                return value
            row = db.execute("SELECT body FROM state WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def incident(self, code):
        with self.connect() as db:
            db.execute("INSERT INTO incidents(occurred,code) VALUES(?,?)", (time.time(), code))

    def counts(self):
        with self.connect() as db:
            return dict(db.execute("SELECT count(*) total, coalesce(sum(acknowledged=0),0) pending FROM events").fetchone())


class Lane:
    """One stable episode between confirmed clear readings. Injected clock for tests."""
    def __init__(self, *, stable_seconds=5, empty_max=500, tolerance=50, clear_polls=3):
        self.stable_seconds, self.empty_max, self.tolerance = stable_seconds, empty_max, tolerance
        self.clear_polls = clear_polls
        self.armed = False
        self.clear_count = 0
        self.since = self.weight = self.last_token = None
        self.last_time = None
        self.last_valid_time = None

    def gap(self):
        self.armed = False
        self.clear_count = 0
        self.since = self.weight = self.last_token = None
        self.last_valid_time = None

    def unavailable(self, now):
        # A single network timeout does not prove that another vehicle arrived.
        # Restart stability confirmation, retaining occupancy only within the
        # bounded observation window. A longer outage requires a fresh clear.
        if self.last_valid_time is None or now - self.last_valid_time > 5:
            self.gap()
        else:
            self.clear_count = 0
            self.since = self.weight = self.last_token = None

    def observe(self, observation, now):
        if self.last_time is not None and now - self.last_time > 5:
            self.gap()
        if self.last_valid_time is not None and now - self.last_valid_time > 5:
            self.gap()
        self.last_time = now
        if observation.state not in {"ready", "unstable"} or observation.weight_kg is None:
            self.unavailable(now)
            return False
        token = observation.updated_at
        if not token or token == self.last_token:
            return False
        self.last_token = token
        self.last_valid_time = now
        weight = float(observation.weight_kg)
        if not observation.stable:
            self.clear_count = 0
            self.since = self.weight = None
            return False
        if weight <= self.empty_max:
            self.clear_count += 1
            self.since = self.weight = None
            if self.clear_count >= self.clear_polls:
                self.armed = True
            return False
        self.clear_count = 0
        if not self.armed:
            return False
        if self.since is None or abs(weight - self.weight) > self.tolerance:
            self.since, self.weight = now, weight
            return False
        return now - self.since >= self.stable_seconds

    def captured(self):
        self.armed = False
        self.since = self.weight = None
