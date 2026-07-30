"""Background reconciliation for ApiPay invoices.

Webhooks remain the fast path. This module is the durable fallback for missed
deliveries (including ApiPay's webhook circuit breaker) and for documented
late payments after a cancelled, expired, errored, or superseded invoice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db.models import Q
from django.utils import timezone

from .apipay import (
    apply_invoice_status,
    check_invoice_statuses,
    recover_invoice_issue_mapping,
)
from .models import ApiPayInvoice

log = logging.getLogger(__name__)


# Active provider operations must remain recoverable regardless of age. The
# lookback is only a bounded late-payment window for already closed invoices.
ACTIVE_INVOICE_STATUSES = (
    "creating",
    "processing",
    "pending",
    "cancelling",
)
LATE_PAYMENT_INVOICE_STATUSES = (
    "cancelled",
    "expired",
    "error",
    "superseded",
)
POLLABLE_INVOICE_STATUSES = (
    *ACTIVE_INVOICE_STATUSES,
    *LATE_PAYMENT_INVOICE_STATUSES,
)


@dataclass
class ReconciliationStats:
    issue_selected: int = 0
    issue_recovered: int = 0
    issue_released: int = 0
    issue_failed: int = 0
    selected: int = 0
    batches: int = 0
    fetched: int = 0
    changed: int = 0
    unchanged: int = 0
    missing: int = 0
    unexpected: int = 0
    failed: int = 0


def _response_invoices(response: object) -> list[dict[str, Any]]:
    if not isinstance(response, dict):
        raise TypeError("ApiPay status response must be an object")
    invoices = response.get("invoices")
    if not isinstance(invoices, list):
        raise TypeError("ApiPay status response must contain invoices[]")
    if not all(isinstance(invoice, dict) for invoice in invoices):
        raise TypeError("ApiPay invoices[] must contain objects")
    return invoices


def _payloads_by_id(
    payloads: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], int]:
    result: dict[int, dict[str, Any]] = {}
    duplicate_ids: set[int] = set()
    malformed = 0
    for payload in payloads:
        try:
            invoice_id = int(payload["id"])
        except (KeyError, TypeError, ValueError):
            malformed += 1
            log.error("ApiPay reconciliation ignored invoice without a valid id")
            continue
        if invoice_id in duplicate_ids:
            malformed += 1
            continue
        if invoice_id in result:
            malformed += 1
            duplicate_ids.add(invoice_id)
            log.error(
                "ApiPay reconciliation ignored duplicate invoice id=%s",
                invoice_id,
            )
            # An ambiguous response must not be applied.
            result.pop(invoice_id, None)
            continue
        result[invoice_id] = payload
    return result, malformed


def reconcile_apipay_invoices(
    *,
    batch_size: int = 100,
    limit: int | None = None,
    request_budget: int | None = None,
    stale_after: timedelta = timedelta(seconds=30),
    lookback: timedelta = timedelta(hours=72),
    now=None,
) -> ReconciliationStats:
    """Poll and transactionally apply authoritative provider invoice states.

    The candidate list is snapshotted before making network calls so one bad
    batch cannot create a tight retry loop. ``updated_at`` is only a polling
    throttle: ``apply_invoice_status`` updates it for returned invoices, and
    omitted IDs are touched explicitly. Ambiguous QR release age is based on
    immutable ``created_at`` inside ``recover_qr_invoice_mapping``.
    """

    batch_size = max(1, min(int(batch_size), 500))
    if limit is not None:
        limit = max(1, int(limit))
    bounded_requests = request_budget is not None
    requests_remaining = (
        max(0, int(request_budget))
        if request_budget is not None
        else None
    )
    stale_after = max(stale_after, timedelta(0))
    lookback = max(lookback, timedelta(seconds=1))
    observed_at = now or timezone.now()
    stale_cutoff = observed_at - stale_after
    lookback_cutoff = observed_at - lookback

    # A lost create response leaves no provider invoice_id to poll. Retry one
    # stale local reservation with its original idempotency key; ApiPay either
    # creates it now or returns the existing invoice via duplicate-key recovery.
    issue_candidate = None
    if requests_remaining is None or requests_remaining > 0:
        issue_candidate = (
            ApiPayInvoice.objects.filter(
                invoice_id__isnull=True,
                status="creating",
                updated_at__lte=stale_cutoff,
            )
            .select_related("payment")
            .order_by("updated_at", "pk")
            .first()
        )
    stats = ReconciliationStats()
    issue_attempted = issue_candidate is not None
    if issue_candidate is not None:
        stats.issue_selected = 1
        issue_cursor = issue_candidate.updated_at
        try:
            allocated_requests = requests_remaining
            recovered = recover_invoice_issue_mapping(
                issue_candidate,
                max_pages=allocated_requests,
                # A sparse money row is safely polled as a mapped invoice next
                # cycle; do not spend a hidden GET outside the hard budget.
                hydrate_money_response=not bounded_requests,
            )
            if bounded_requests:
                # Pagination can use any/all of this allocation. Reserve it
                # conservatively so the hard cap is never exceeded.
                requests_remaining = 0
        except Exception:
            if bounded_requests:
                requests_remaining = 0
            # A poison provider response or exhausted pagination budget must
            # not monopolize the oldest issue forever. CAS avoids overwriting
            # a concurrent mapping or a newer successful observation.
            ApiPayInvoice.objects.filter(
                pk=issue_candidate.pk,
                invoice_id__isnull=True,
                status="creating",
                updated_at=issue_cursor,
            ).update(updated_at=observed_at)
            stats.issue_failed = 1
            stats.failed += 1
            log.exception(
                "ApiPay reconciliation could not recover invoice issue id=%s",
                issue_candidate.pk,
            )
        else:
            if recovered is not None and recovered.invoice_id is not None:
                stats.issue_recovered = 1
            else:
                issue_candidate.refresh_from_db(fields=["status"])
                if issue_candidate.status == "error":
                    stats.issue_released = 1

    mapped_limit = limit
    if bounded_requests:
        assert requests_remaining is not None
        request_limited_rows = requests_remaining * batch_size
        mapped_limit = (
            request_limited_rows
            if mapped_limit is None
            else min(mapped_limit, request_limited_rows)
        )
    elif issue_attempted and mapped_limit is not None:
        # The command budgets mapped invoices in provider batches. One create
        # retry consumed one such request slot.
        mapped_limit = max(0, mapped_limit - batch_size)

    candidate_query = (
        ApiPayInvoice.objects.filter(
            invoice_id__isnull=False,
            updated_at__lte=stale_cutoff,
        )
        .filter(
            Q(status__in=ACTIVE_INVOICE_STATUSES)
            | Q(
                status__in=LATE_PAYMENT_INVOICE_STATUSES,
                created_at__gte=lookback_cutoff,
            )
        )
        .only("id", "invoice_id", "status", "updated_at")
        .order_by("updated_at", "pk")
    )
    if mapped_limit is not None:
        candidate_query = candidate_query[:mapped_limit]
    candidates = list(candidate_query)
    stats.selected = len(candidates)

    for offset in range(0, len(candidates), batch_size):
        batch = candidates[offset : offset + batch_size]
        requested_ids = [int(record.invoice_id) for record in batch]
        expected = {int(record.invoice_id): record for record in batch}
        stats.batches += 1

        try:
            response = check_invoice_statuses(requested_ids)
            payloads = _response_invoices(response)
            by_id, malformed = _payloads_by_id(payloads)
        except Exception:
            stats.failed += len(batch)
            log.exception(
                "ApiPay reconciliation batch failed invoice_ids=%s",
                requested_ids,
            )
            continue

        stats.fetched += len(payloads)
        stats.failed += malformed
        unexpected_ids = set(by_id) - set(expected)
        if unexpected_ids:
            stats.unexpected += len(unexpected_ids)
            log.warning(
                "ApiPay reconciliation returned unexpected invoice_ids=%s",
                sorted(unexpected_ids),
            )

        missing_records = [
            record for invoice_id, record in expected.items() if invoice_id not in by_id
        ]
        if missing_records:
            stats.missing += len(missing_records)
            log.warning(
                "ApiPay reconciliation omitted invoice_ids=%s",
                [record.invoice_id for record in missing_records],
            )
            # The endpoint intentionally omits unknown/foreign IDs. Throttle
            # repeated checks without changing any payment or provider status.
            ApiPayInvoice.objects.filter(
                pk__in=[record.pk for record in missing_records]
            ).update(updated_at=observed_at)

        for invoice_id, record in expected.items():
            payload = by_id.get(invoice_id)
            if payload is None:
                continue
            try:
                changed = apply_invoice_status(record, payload)
            except Exception:
                stats.failed += 1
                log.exception(
                    "ApiPay reconciliation could not apply invoice id=%s",
                    invoice_id,
                )
                continue
            if changed:
                stats.changed += 1
            else:
                stats.unchanged += 1

    return stats
