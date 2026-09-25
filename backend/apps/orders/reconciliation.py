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

from django.db.models import F, Q
from django.utils import timezone

from .apipay import (
    ApiPayConfigurationError,
    CLOSED_INVOICE_STATUSES,
    ApiPayCredentials,
    apply_invoice_status,
    check_invoice_statuses,
    credentials_for_department_code,
    positive_provider_id,
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
    # Счета отдела без ключа ApiPay: это настройка, а не сбой сверки —
    # повтор с backoff её не исправит, и монитор из-за неё не «болеет».
    unconfigured: int = 0


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
            invoice_id = positive_provider_id(payload.get("id"))
        except ValueError:
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


def _department_batches(
    candidates: list[ApiPayInvoice],
    batch_size: int,
    stats: ReconciliationStats,
):
    """Батчи внутри одного отдела: у каждого отдела свой ключ ApiPay.

    Группа без ключа учитывается как ``unconfigured`` и не трогает ``updated_at``:
    как только суперюзер подключит Kaspi отделу, счета сверятся в следующем цикле.
    """
    grouped: dict[str, list[ApiPayInvoice]] = {}
    for record in candidates:
        grouped.setdefault(record.department_code or "", []).append(record)
    for department_code, records in grouped.items():
        try:
            credentials: ApiPayCredentials = credentials_for_department_code(
                department_code
            )
        except ApiPayConfigurationError as exc:
            stats.unconfigured += len(records)
            log.warning(
                "ApiPay reconciliation skipped department=%r: %s",
                department_code,
                exc,
            )
            continue
        for offset in range(0, len(records), batch_size):
            yield credentials, records[offset : offset + batch_size]


def reconcile_apipay_invoices(
    *,
    request_budget: int,
    batch_size: int = 100,
    stale_after: timedelta = timedelta(seconds=30),
    lookback: timedelta = timedelta(hours=72),
    now=None,
) -> ReconciliationStats:
    """Poll and transactionally apply authoritative provider invoice states.

    ``request_budget`` is a hard cap on provider requests for this call; the
    runner (:mod:`.reconciliation_runner`) already normalizes it together with
    ``batch_size``, staleness and lookback. The candidate list is snapshotted
    before making network calls so one bad batch cannot create a tight retry
    loop. ``updated_at`` is only a polling throttle: ``apply_invoice_status``
    updates it for returned invoices, and omitted IDs are touched explicitly.
    Ambiguous QR release age is based on immutable ``created_at`` inside
    ``recover_invoice_issue_mapping``.
    """

    requests_remaining = max(0, int(request_budget))
    observed_at = now or timezone.now()
    stale_cutoff = observed_at - stale_after
    lookback_cutoff = observed_at - lookback

    # A lost create response leaves no provider invoice_id to poll. Retry one
    # stale local reservation with its original idempotency key; ApiPay either
    # creates it now or returns the existing invoice via duplicate-key recovery.
    issue_candidate = None
    if requests_remaining > 0:
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
    if issue_candidate is not None:
        stats.issue_selected = 1
        issue_cursor = issue_candidate.updated_at
        # Pagination can use any/all of the budget. Reserve it conservatively
        # so the hard cap is never exceeded.
        allocated_requests, requests_remaining = requests_remaining, 0
        try:
            recovered = recover_invoice_issue_mapping(
                issue_candidate,
                max_pages=allocated_requests,
                # A sparse money row is safely polled as a mapped invoice next
                # cycle; do not spend a hidden GET outside the hard budget.
                hydrate_money_response=False,
            )
        except Exception:
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

    candidate_query = (
        ApiPayInvoice.objects.filter(
            invoice_id__isnull=False,
            updated_at__lte=stale_cutoff,
        )
        .filter(
            Q(status__in=ACTIVE_INVOICE_STATUSES)
            | Q(
                status__in=CLOSED_INVOICE_STATUSES,
                created_at__gte=lookback_cutoff,
            )
        )
        .only("id", "invoice_id", "status", "updated_at")
        .annotate(department_code=F("payment__order__department"))
        .order_by("updated_at", "pk")
    )
    # Each status request checks one batch of mapped invoices.
    candidates = list(candidate_query[: requests_remaining * batch_size])
    stats.selected = len(candidates)

    for credentials, batch in _department_batches(candidates, batch_size, stats):
        requested_ids = [int(record.invoice_id) for record in batch]
        expected = {int(record.invoice_id): record for record in batch}
        stats.batches += 1

        try:
            response = check_invoice_statuses(
                requested_ids, credentials=credentials
            )
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
