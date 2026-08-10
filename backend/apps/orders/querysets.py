"""Persistence plans for order API serialization.

The serializer traverses several related objects. Keeping the loading plan in
one place prevents list and detail endpoints from drifting back into N+1
queries when serializer fields evolve.
"""

from datetime import timedelta

from django.db.models import Prefetch, Q, QuerySet
from django.utils import timezone

from .models import Order, Payment, StatusChangeRequest


def for_post_board(
    queryset: QuerySet[Order],
    completed_order_days: int,
) -> QuerySet[Order]:
    """Active loading work plus the configured window of shipped orders."""
    since = timezone.localdate() - timedelta(
        days=max(0, completed_order_days - 1)
    )
    return queryset.filter(
        Q(status__in=("confirmed", "arrived", "loading", "loaded"))
        | Q(status="shipped", shipment__shipped_at__date__gte=since)
    )


def with_payment_api_relations(
    queryset: QuerySet[Payment], *, order_context: bool = False
) -> QuerySet[Payment]:
    """Loading plan for ``PaymentSerializer`` and its queue subclass.

    ``effective_status``/``can_issue`` read the provider invoice on every row,
    and ``can_restore`` compares the sibling payments of the same order against
    ``order.total_amount``, which sums the order items. All three relations
    must therefore be loaded eagerly. ``order_context`` adds the order/client
    columns the queue and transaction lists display.
    """
    queryset = queryset.select_related("apipay_invoice").prefetch_related(
        "order__items",
        "order__payments",
        "apipay_invoice__refunds",
        "payment_refunds__requested_by",
    )
    if order_context:
        queryset = queryset.select_related(
            "order__client__user", "order__store",
            "recorded_by", "received_by", "confirmed_by",
        )
    return queryset


def with_order_api_relations(queryset: QuerySet[Order]) -> QuerySet[Order]:
    payments = Payment.objects.select_related(
        "recorded_by", "received_by", "confirmed_by", "apipay_invoice"
    ).prefetch_related(
        "apipay_invoice__refunds", "payment_refunds__requested_by"
    )
    status_requests = StatusChangeRequest.objects.select_related(
        "requested_by", "decided_by"
    )
    return (
        queryset
        .select_related(
            "client__user", "store", "shipment", "debt_override_by", "deleted_by"
        )
        .prefetch_related(
            "items__product",
            "client__prices",
            Prefetch("payments", queryset=payments),
            Prefetch("status_requests", queryset=status_requests),
        )
    )
