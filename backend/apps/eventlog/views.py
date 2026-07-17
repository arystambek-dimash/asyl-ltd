from rest_framework import viewsets, mixins
from django.db.models import Q
from apps.common.permissions import PermViewSetMixin
from apps.common.query_params import parse_iso_date, validate_date_range
from apps.orders.models import Order
from .models import EventLog
from .serializers import EventLogSerializer


class EventLogViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = EventLogSerializer
    required_perms = {"list": "events.view"}

    def get_queryset(self):
        visible_orders = Order.objects.all()
        # Системные события и события заказов доступны в едином журнале.
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
