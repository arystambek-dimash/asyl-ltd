"""Keep each configured lane polling without waiting for other camera PCs."""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor

from . import ai, shipping_automation
from .models import ShippingTransportCamera

log = logging.getLogger(__name__)
MAX_CONCURRENT_LANES = 32


class ShippingTransportScheduler:
    """Main-thread scheduler with one bounded, non-overlapping call per lane.

    Heartbeats belong to the supervisor loop. A blocked lane does not prevent
    other lanes from observing fresh frames; its own body timestamp still ages
    out in the API. There is no unbounded executor queue or catch-up burst.
    """

    def __init__(self, interval: float, *, max_workers: int = MAX_CONCURRENT_LANES):
        if not 1 <= max_workers <= MAX_CONCURRENT_LANES:
            raise ValueError("max_workers must be between 1 and 32")
        self.interval = interval
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="shipping-transport"
        )
        self._in_flight: dict[int, Future] = {}
        self._next_due: dict[int, float] = {}
        self._closed = False

    def tick(self) -> dict:
        if self._closed:
            raise RuntimeError("Shipping transport scheduler is closed")
        processed = errors = 0
        for binding_id, future in list(self._in_flight.items()):
            if not future.done():
                continue
            del self._in_flight[binding_id]
            processed += 1
            try:
                errors += bool(future.result())
            except Exception:
                log.exception("Shipping transport lane failed: binding=%s", binding_id)
                errors += 1

        ids = (
            set(ShippingTransportCamera.objects.values_list("id", flat=True))
            if ai.enabled()
            else set()
        )
        self._next_due = {
            binding_id: due
            for binding_id, due in self._next_due.items()
            if binding_id in ids
        }
        now = time.monotonic()
        # Older due lanes get priority if the configured lane count exceeds
        # capacity. Unseen lanes precede a lane that has already been polled.
        due_ids = sorted(ids, key=lambda item: (self._next_due.get(item, -1), item))
        for binding_id in due_ids:
            if len(self._in_flight) >= self.max_workers:
                break
            if binding_id in self._in_flight:
                continue
            if self._next_due.get(binding_id, -1) > now:
                continue
            self._in_flight[binding_id] = self._executor.submit(
                shipping_automation._worker, binding_id
            )
            self._next_due[binding_id] = now + self.interval
        return {"processed": processed, "errors": errors}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Finish bounded OCR/counting requests and their database reconciliation
        # before the service exits. No next poll is submitted during shutdown.
        self._executor.shutdown(wait=True, cancel_futures=True)
