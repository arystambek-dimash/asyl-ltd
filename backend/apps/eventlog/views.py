from rest_framework import viewsets, mixins
from apps.rbac.permissions import PermViewSetMixin
from .models import EventLog
from .serializers import EventLogSerializer


class EventLogViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = EventLogSerializer
    required_perms = {"list": "events.view"}

    def get_queryset(self):
        qs = EventLog.objects.all()
        p = self.request.query_params
        if p.get("order"):
            qs = qs.filter(order_id=p["order"])
        if p.get("event_type"):
            qs = qs.filter(event_type=p["event_type"])
        if p.get("search"):
            qs = qs.filter(message__icontains=p["search"])
        if p.get("date_from"):
            qs = qs.filter(created_at__date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(created_at__date__lte=p["date_to"])
        return qs
