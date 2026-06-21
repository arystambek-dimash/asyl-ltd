from rest_framework import viewsets, mixins
from accounts.permissions import IsStaff
from .models import EventLog
from .serializers import EventLogSerializer


class EventLogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = EventLogSerializer
    permission_classes = [IsStaff]

    def get_queryset(self):
        qs = EventLog.objects.all()
        p = self.request.query_params
        order = p.get("order")
        etype = p.get("event_type")
        search = p.get("search")
        date_from = p.get("date_from")
        date_to = p.get("date_to")
        if order:
            qs = qs.filter(order_id=order)
        if etype:
            qs = qs.filter(event_type=etype)
        if search:
            qs = qs.filter(message__icontains=search)
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        return qs
