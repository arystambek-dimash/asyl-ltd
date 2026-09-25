"""Process scaffolding shared by the truck and the wagon collectors.

Both run next to their scale as a standalone process: one outbox directory
guarded by a lock file, a 1 s poll loop that survives a briefly locked SQLite
file, a snapshot from the isolated video relay and the same shutdown order.
"""

import fcntl
import os
import signal
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from django.conf import settings
from apps.grain import scale
from .outbox import Outbox, is_busy

FRAME_MAX_BYTES = 4 * 1024 * 1024


def fetch_frame(camera):
    """One JPEG of the camera's main stream, or None if the relay sent something else.

    Transport failures propagate: each collector decides what a failed
    snapshot means for its own event.
    """
    src = urllib.parse.urlencode({"src": camera + "main"})
    request = urllib.request.Request(settings.GO2RTC_API_URL.rstrip("/") + "/api/frame.jpeg?" + src)
    # An isolated video relay, never the web deployment's relay.
    with scale.open_local_request(request, timeout=4) as response:
        value = response.read(FRAME_MAX_BYTES + 1)
    return value if len(value) <= FRAME_MAX_BYTES and value.startswith(b"\xff\xd8") else None


class CollectorProcess:
    """``poll`` and ``close`` of a collector; subclasses supply ``_poll``, ``pool`` and ``writer``."""

    busy_message = "outbox_busy_retry"
    last_queue_error = 0.0

    def poll(self):
        try:
            self._poll()
        except sqlite3.OperationalError as exc:
            if not is_busy(exc):
                raise
            # Do not restart the scale state because the importer briefly
            # owns a SQLite lock. The next tick retries with the same state.
            if time.monotonic() - self.last_queue_error > 30:
                print(self.busy_message, flush=True)
                self.last_queue_error = time.monotonic()

    def close(self):
        self.pool.shutdown(wait=True)
        # Each flush is bounded by SQLite's busy timeout; there is no infinite
        # worker retry loop holding process shutdown. Normal shutdown drains all
        # captured evidence before the writer exits.
        drained = self.writer.drain(timeout=10)
        self.writer.shutdown()
        if not drained:
            print("outbox_storage_unavailable_unflushed_evidence", flush=True)


def run(make_collector, *, default_directory, started_incident):
    directory = Path(os.environ.get("WEIGHBRIDGE_OUTBOX_DIR", default_directory))
    box = Outbox(directory)
    lock = (directory / "collector.lock").open("a")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("collector_already_running", flush=True)
        raise SystemExit(1)
    box.recover()
    box.incident(started_incident)
    collector = make_collector(box)
    stopped = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    try:
        while not stopped.is_set():
            started = time.monotonic()
            collector.poll()
            stopped.wait(max(0, 1 - (time.monotonic() - started)))
    finally:
        collector.close()
