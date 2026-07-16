from rest_framework import viewsets, mixins
from django.db.models import Q
from apps.common.permissions import PermViewSetMixin
from apps.common.query_params import parse_iso_date, validate_date_range
from apps.orders.models import Order
from apps.rbac.scoping import scope_by_department
from .models import EventLog
from .serializers import EventLogSerializer


class EventLogViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = EventLogSerializer
    required_perms = {"list": "events.view"}

    def get_queryset(self):
        visible_orders = scope_by_department(
            Order.objects.all(), self.request.user, "events.view",
            owner_field="client__manager",
        )
        # System/warehouse events have no order and remain global; events tied
        # to an order inherit that order's department visibility.
        qs = EventLog.objects.filter(
            Q(order__isnull=True) | Q(order__in=visible_orders))
        p = self.request.query_params
        if p.get("order"):
            qs = qs.filter(order_id=p["order"])
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
