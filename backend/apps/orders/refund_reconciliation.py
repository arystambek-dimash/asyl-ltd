"""Durable reconciliation for ApiPay refunds.

ApiPay documents refund creation plus two read paths:
GET /invoices/{id}/refunds and GET /refunds. It does not document a
GET-by-refund-id endpoint or an idempotency key for refund POST requests.
Consequently an ambiguous POST must never be retried: this module observes the
invoice refund list, correlates it to the local reservation, and only releases
an old reservation after a complete authoritative snapshot proves it absent.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.eventlog.services import log_event

from .apipay import (
    _sync_refund_totals,
    apply_refund_status,
    get_invoice_refunds,
)
from .models import ApiPayInvoice, ApiPayRefund, Order, Payment, PaymentRefund

log = logging.getLogger(__name__)

REFUND_DISCOVERY_STATUSES = ("paid", "partially_refunded")
DEFAULT_REFUND_SWEEP_STALE_AFTER = timedelta(minutes=15)


@dataclass
class RefundReconciliationStats:
    selected: int = 0
    fetched: int = 0
    changed: int = 0
    unchanged: int = 0
    released_orphans: int = 0
    ambiguous: int = 0
    incomplete: int = 0
    failed: int = 0


def _provider_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    return (
        parsed
        if timezone.is_aware(parsed)
        else timezone.make_aware(parsed, dt_timezone.utc)
    )


def _provider_id(payload: dict[str, Any]) -> int:
    try:
        raw = payload["id"]
        if isinstance(raw, bool):
            raise TypeError
        decimal_value = Decimal(str(raw))
        if (
            not decimal_value.is_finite()
            or decimal_value != decimal_value.to_integral_value()
        ):
            raise ValueError
        value = int(decimal_value)
    except (
        InvalidOperation,
        KeyError,
        OverflowError,
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError("refund id is invalid") from exc
    if value <= 0:
        raise ValueError("refund id must be positive")
    return value


def _provider_amount(payload: dict[str, Any]) -> Decimal:
    try:
        value = Decimal(str(payload["amount"])).quantize(Decimal("0.01"))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("refund amount is invalid") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError("refund amount must be positive")
    return value


def _provider_status(payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or "")
    if status not in {"pending", "processing", "completed", "failed"}:
        raise ValueError("refund status is invalid")
    return status


def _local_sort_key(refund: PaymentRefund):
    return refund.created_at, refund.pk


def _provider_sort_key(payload: dict[str, Any]):
    observed = _provider_datetime(payload.get("created_at"))
    return (
        observed or datetime.min.replace(tzinfo=dt_timezone.utc),
        _provider_id(payload),
    )


def _pair_groups(
    locals_left: dict[int, PaymentRefund],
    providers_left: dict[int, dict[str, Any]],
    *,
    local_key: Callable[[PaymentRefund], Hashable | None],
    provider_key: Callable[[dict[str, Any]], Hashable | None],
) -> dict[int, int]:
    """Pair equal provider/local groups in stable creation order."""
    local_groups: dict[Hashable, list[PaymentRefund]] = defaultdict(list)
    provider_groups: dict[Hashable, list[dict[str, Any]]] = defaultdict(list)
    for local in locals_left.values():
        key = local_key(local)
        if key is not None:
            local_groups[key].append(local)
    for payload in providers_left.values():
        key = provider_key(payload)
        if key is not None:
            provider_groups[key].append(payload)

    matches: dict[int, int] = {}
    for key in local_groups.keys() & provider_groups.keys():
        local_rows = sorted(local_groups[key], key=_local_sort_key)
        provider_rows = sorted(provider_groups[key], key=_provider_sort_key)
        for local, payload in zip(local_rows, provider_rows):
            provider_id = _provider_id(payload)
            matches[provider_id] = local.pk
            locals_left.pop(local.pk, None)
            providers_left.pop(provider_id, None)
    return matches


def _correlation_matches(
    record: ApiPayInvoice,
    payloads: list[dict[str, Any]],
) -> dict[int, int]:
    """Match provider rows to local unlinked reservations without duplication.

    Exact amount+reason is preferred. ApiPay echoes both reason and created_at.
    Legacy same-amount/same-reason concurrency is paired in creation order. Any
    remaining rows are paired by amount, which safely preserves money totals
    even if an old provider omitted the optional reason.
    """
    local_rows = list(
        PaymentRefund.objects.filter(
            payment_id=record.payment_id,
            method="apipay",
            status="pending",
            provider_refund__isnull=True,
        ).order_by("created_at", "pk")
    )
    linked_provider_ids = set(
        PaymentRefund.objects.filter(
            payment_id=record.payment_id,
            provider_refund__isnull=False,
        ).values_list("provider_refund__refund_id", flat=True)
    )
    locals_left = {row.pk: row for row in local_rows}
    providers_left = {
        _provider_id(payload): payload
        for payload in payloads
        if _provider_id(payload) not in linked_provider_ids
    }

    matches = _pair_groups(
        locals_left,
        providers_left,
        local_key=lambda row: (row.amount, row.reason.strip()),
        provider_key=lambda row: (
            _provider_amount(row),
            str(row.get("reason") or "").strip(),
        ),
    )
    matches.update(_pair_groups(
        locals_left,
        providers_left,
        local_key=lambda row: row.amount,
        provider_key=_provider_amount,
    ))
    return matches


def _validated_snapshot(
    response: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    refunds = response.get("refunds")
    if not isinstance(refunds, list):
        raise TypeError("ApiPay refund response must contain refunds[]")
    try:
        raw_total = response["total"]
        if isinstance(raw_total, bool):
            raise TypeError
        decimal_total = Decimal(str(raw_total))
        if (
            not decimal_total.is_finite()
            or decimal_total != decimal_total.to_integral_value()
        ):
            raise ValueError
        total = int(decimal_total)
    except (
        InvalidOperation,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError("ApiPay refund response must contain total") from exc
    if total < 0:
        raise ValueError("ApiPay refund total cannot be negative")

    valid: list[dict[str, Any]] = []
    seen: set[int] = set()
    complete = total == len(refunds)
    for payload in refunds:
        if not isinstance(payload, dict):
            complete = False
            continue
        try:
            provider_id = _provider_id(payload)
            _provider_amount(payload)
            _provider_status(payload)
        except ValueError:
            complete = False
            continue
        if provider_id in seen:
            complete = False
            continue
        seen.add(provider_id)
        valid.append(payload)
    return valid, complete and len(valid) == total


def _release_absent_orphans(
    record: ApiPayInvoice,
    *,
    snapshot_complete: bool,
    observed_at,
    orphan_grace: timedelta,
) -> tuple[int, int]:
    unlinked = list(
        PaymentRefund.objects.filter(
            payment_id=record.payment_id,
            method="apipay",
            status="pending",
            provider_refund__isnull=True,
        ).order_by("created_at", "pk")
    )
    if not unlinked:
        return 0, 0
    if not snapshot_complete:
        return 0, len(unlinked)

    provider_unlinked_amounts = set(
        ApiPayRefund.objects.filter(
            invoice=record,
            payment_refund__isnull=True,
        ).values_list("amount", flat=True)
    )
    cutoff = observed_at - max(orphan_grace, timedelta(0))
    releasable = [
        row
        for row in unlinked
        if row.created_at <= cutoff and row.amount not in provider_unlinked_amounts
    ]
    if not releasable:
        return 0, len(unlinked)

    with transaction.atomic():
        order = Order.all_objects.select_for_update().get(
            pk=record.payment.order_id
        )
        payment = Payment.objects.select_for_update().get(pk=record.payment_id)
        locked = list(
            PaymentRefund.objects.select_for_update().filter(
                pk__in=[row.pk for row in releasable],
                method="apipay",
                status="pending",
                provider_refund__isnull=True,
            )
        )
        for row in locked:
            row.status = "failed"
            row.save(update_fields=["status", "updated_at"])
            log_event(
                "payment",
                (
                    "Резерв возврата снят: платёжный сервис не показывает "
                    "созданный возврат после периода сверки"
                ),
                order=order,
                payload={
                    "action": "apipay_refund_not_observed",
                    "payment_id": payment.pk,
                    "refund_id": row.pk,
                    "amount": str(row.amount),
                    "reason": row.reason,
                },
            )
        payment.order = order
        _sync_refund_totals(payment, order)
    return len(locked), len(unlinked) - len(locked)


def _oldest_refund_candidates(queryset, limit: int) -> list[ApiPayInvoice]:
    if limit <= 0:
        return []
    return list(
        queryset.order_by(
            F("refund_checked_at").asc(nulls_first=True),
            "pk",
        )[:limit]
    )


def _refund_reconciliation_candidates(
    *,
    limit: int,
    observed_at,
    sweep_stale_after: timedelta,
) -> list[ApiPayInvoice]:
    """Share a bounded budget between urgent local work and discovery.

    Local pending/ambiguous refunds always receive at least half the budget.
    The other lane discovers provider-side refunds even when no local row
    exists. Unused capacity from either lane is filled by the other.
    """
    base = (
        ApiPayInvoice.objects.filter(invoice_id__isnull=False)
        .select_related("payment__order")
    )
    pending_filter = (
        Q(
            payment__payment_refunds__method="apipay",
            payment__payment_refunds__status="pending",
        )
        | Q(refunds__status__in=("pending", "processing"))
    )
    pending = base.filter(pending_filter).distinct()

    sweep_cutoff = observed_at - max(sweep_stale_after, timedelta(0))
    discovery_due = (
        Q(status__in=REFUND_DISCOVERY_STATUSES)
        & (
            Q(refund_checked_at__isnull=True)
            | Q(refund_checked_at__lte=sweep_cutoff)
        )
    )

    if limit == 1:
        # A hard one-row pending quota would permanently suppress discovery
        # whenever any local refund stayed pending. Sharing the same persisted
        # cursor makes successive one-row iterations rotate across both lanes.
        return _oldest_refund_candidates(
            base.filter(pending_filter | discovery_due).distinct(),
            1,
        )

    pending_quota = max(1, (limit + 1) // 2)
    selected = _oldest_refund_candidates(pending, pending_quota)
    selected_ids = {record.pk for record in selected}

    discovery = (
        base.filter(status__in=REFUND_DISCOVERY_STATUSES)
        .filter(
            Q(refund_checked_at__isnull=True)
            | Q(refund_checked_at__lte=sweep_cutoff)
        )
        .exclude(pk__in=selected_ids)
    )
    discovered = _oldest_refund_candidates(
        discovery,
        limit - len(selected),
    )
    selected.extend(discovered)
    selected_ids.update(record.pk for record in discovered)

    # If the discovery lane had fewer rows than its reserved share, let urgent
    # local reconciliation consume the otherwise idle provider-call budget.
    remaining = limit - len(selected)
    if remaining:
        selected.extend(
            _oldest_refund_candidates(
                pending.exclude(pk__in=selected_ids),
                remaining,
            )
        )
    return selected


def reconcile_apipay_refunds(
    *,
    limit: int = 25,
    orphan_grace: timedelta = timedelta(minutes=15),
    sweep_stale_after: timedelta = DEFAULT_REFUND_SWEEP_STALE_AFTER,
    now=None,
) -> RefundReconciliationStats:
    """Poll known refunds and discover provider-side refunds after lost hooks.

    A full successful GET snapshot can prove that an old unlinked local
    reservation never became a provider refund. Partial/malformed snapshots
    never release money and remain visible for manual/provider investigation.
    """
    observed_at = now or timezone.now()
    limit = max(1, min(int(limit), 500))
    candidates = _refund_reconciliation_candidates(
        limit=limit,
        observed_at=observed_at,
        sweep_stale_after=sweep_stale_after,
    )
    stats = RefundReconciliationStats(selected=len(candidates))

    for record in candidates:
        try:
            try:
                response = get_invoice_refunds(record)
                payloads, complete = _validated_snapshot(response)
                matches = _correlation_matches(record, payloads)
            except Exception:
                stats.failed += 1
                log.exception(
                    "ApiPay refund reconciliation failed invoice_id=%s",
                    record.invoice_id,
                )
                continue

            stats.fetched += len(payloads)
            if not complete:
                stats.incomplete += 1
            for payload in payloads:
                provider_id = _provider_id(payload)
                try:
                    changed = apply_refund_status(
                        record,
                        payload,
                        generic_refund_id=matches.get(provider_id),
                        allow_automatic_link=False,
                    )
                except Exception:
                    complete = False
                    stats.failed += 1
                    log.exception(
                        "ApiPay refund reconciliation could not apply "
                        "invoice_id=%s refund_id=%s",
                        record.invoice_id,
                        provider_id,
                    )
                    continue
                if changed:
                    stats.changed += 1
                else:
                    stats.unchanged += 1

            try:
                released, ambiguous = _release_absent_orphans(
                    record,
                    snapshot_complete=complete,
                    observed_at=observed_at,
                    orphan_grace=orphan_grace,
                )
            except Exception:
                stats.failed += 1
                log.exception(
                    "ApiPay refund reconciliation could not finalize "
                    "invoice_id=%s",
                    record.invoice_id,
                )
                released, ambiguous = 0, 0
            stats.released_orphans += released
            stats.ambiguous += ambiguous
        finally:
            # Rotate both successful and failed observations. Poison provider
            # rows remain retryable after the stale interval but cannot occupy
            # every bounded iteration. A slower overlapping worker must not
            # move the observation cursor backwards.
            ApiPayInvoice.objects.filter(pk=record.pk).filter(
                Q(refund_checked_at__isnull=True)
                | Q(refund_checked_at__lt=observed_at)
            ).update(refund_checked_at=observed_at)

    return stats
