"""Persistence plans for order API serialization.

The serializer traverses several related objects. Keeping the loading plan in
one place prevents list and detail endpoints from drifting back into N+1
queries when serializer fields evolve.
"""

from django.db.models import Prefetch, QuerySet

from .models import Order, Payment, StatusChangeRequest


def with_order_api_relations(queryset: QuerySet[Order]) -> QuerySet[Order]:
    payments = Payment.objects.select_related(
        "recorded_by", "received_by", "confirmed_by", "apipay_invoice"
    ).prefetch_related("apipay_invoice__refunds")
    status_requests = StatusChangeRequest.objects.select_related(
        "requested_by", "decided_by"
    )
    return (
        queryset
        .select_related(
            "client", "store", "shipment", "debt_override_by", "deleted_by"
        )
        .prefetch_related(
            "items__product",
            "client__prices",
            Prefetch("payments", queryset=payments),
            Prefetch("status_requests", queryset=status_requests),
        )
    )
