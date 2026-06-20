from rest_framework import viewsets, mixins
from accounts.permissions import IsStaff
from .models import EventLog
from .serializers import EventLogSerializer


class EventLogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = EventLogSerializer
    permission_classes = [IsStaff]

    def get_queryset(self):
        qs = EventLog.objects.all()
        order = self.request.query_params.get("order")
        etype = self.request.query_params.get("event_type")
        if order and hasattr(EventLog, "order"):
            qs = qs.filter(order_id=order)
        if etype:
            qs = qs.filter(event_type=etype)
        return qs
