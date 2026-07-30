from django.db.models import Q
from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination

from apps.common.permissions import PermViewSetMixin
from apps.common.query_params import parse_iso_date, validate_date_range
from apps.orders.models import Order

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
        visible_orders = Order.objects.all()
        # Системные события и события заказов доступны в едином журнале.
        qs = EventLog.objects.select_related("user").filter(
            Q(order__isnull=True) | Q(order__in=visible_orders)
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
