"""Single-thread FIFO writer: outbox rows are committed in capture order.

Contention delays persistence; it never reorders, drops or replaces a value.
"""
import sqlite3
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from .outbox import is_busy


class OutboxWriter:
    def __init__(self, box):
        self.box = box
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="outbox")
        self.pending_writes = deque()
        self.lock = threading.Lock()
        self.future = None

    def enqueue(self, method, *args, **kwargs):
        with self.lock:
            self.pending_writes.append((method, args, kwargs))
        self.start()

    def pending(self):
        return len(self.pending_writes)

    def start(self):
        with self.lock:
            if self.future is not None:
                if not self.future.done():
                    return
                self.future.result()
            if self.pending_writes:
                self.future = self.executor.submit(self.flush)

    def flush(self):
        while True:
            with self.lock:
                if not self.pending_writes:
                    return
                method, args, kwargs = self.pending_writes[0]
            try:
                getattr(self.box, method)(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if not is_busy(exc):
                    raise
                return  # retained FIFO; the next start() retries off-thread
            with self.lock:
                self.pending_writes.popleft()

    def drain(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while self.pending_writes and time.monotonic() < deadline:
            self.start()
            if self.future is not None:
                self.future.result()
            if self.pending_writes:
                time.sleep(.05)
        return not self.pending_writes

    def shutdown(self):
        self.executor.shutdown(wait=True)
