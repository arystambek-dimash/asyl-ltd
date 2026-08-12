from django.db.models import Q
from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination

from apps.clients.models import Client
from apps.common.permissions import PermViewSetMixin
from apps.common.query_params import parse_iso_date, validate_date_range
from apps.orders.models import Order
from apps.sales.access import assigned_department_id, scope_by_client_department

from .models import EventLog
from .serializers import EventLogSerializer


class EventLogPagination(PageNumberPagination):
    """Bound every response while keeping the complete append-only log reachable."""

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 200


class EventLogViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = EventLogSerializer
    pagination_class = EventLogPagination
    required_perms = {"list": "events.view"}

    def get_queryset(self):
        ownership_department_id = assigned_department_id(self.request.user)
        visible_orders = scope_by_client_department(
            Order.objects.all(),
            self.request.user,
            client_path="client",
        )
        system_events = Q(order__isnull=True)
        if ownership_department_id is not None:
            visible_client_ids = list(
                Client.objects.filter(
                    department_id=ownership_department_id,
                ).values_list("pk", flat=True)
            )
            # Client-level events have no order FK, so their ownership marker
            # lives in the JSON payload. Rows without a verifiable client are
            # hidden fail-closed: an old order event becomes order=NULL after
            # hard deletion and must never be mistaken for a global event.
            system_events &= Q(payload__client_id__in=visible_client_ids)

        # Системные события и события видимых заказов доступны в едином журнале.
        qs = EventLog.objects.select_related("user").filter(
            system_events | Q(order__in=visible_orders)
        )
        p = self.request.query_params
        raw_order_id = p.get("order")
        if raw_order_id:
            try:
                order_id = int(raw_order_id)
                if order_id <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValidationError(
                    {
                        "detail": "Некорректный номер заказа",
                        "code": "bad_order_id",
                    }
                )
            qs = qs.filter(order_id=order_id)
        if p.get("event_type"):
            qs = qs.filter(event_type=p["event_type"])
        if p.get("search"):
            qs = qs.filter(message__icontains=p["search"])
        date_from = parse_iso_date(p.get("date_from"))
        date_to = parse_iso_date(p.get("date_to"))
        validate_date_range(date_from, date_to)
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        return qs
