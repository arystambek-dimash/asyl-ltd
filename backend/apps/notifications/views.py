from rest_framework import viewsets, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.accounts.permissions import IsClientUser
from .models import Notification
from .serializers import NotificationSerializer


class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [IsClientUser]

    def get_queryset(self):
        return Notification.objects.filter(client__user=self.request.user)

    @action(detail=True, methods=["post"], url_path="read")
    def read(self, request, pk=None):
        n = self.get_object()
        n.is_read = True
        n.save(update_fields=["is_read"])
        return Response(self.get_serializer(n).data)
