from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.common.permissions import IsClientUser
from .models import Notification
from .serializers import NotificationSerializer

# Колокольчик грузит список на каждой странице портала, а уведомления копятся
# годами (несколько на каждый заказ) — отдаём только последние.
INBOX_LIMIT = 50


class NotificationViewSet(viewsets.GenericViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [IsClientUser]

    def get_queryset(self):
        return Notification.objects.filter(client__user=self.request.user)

    def list(self, request, *args, **kwargs):
        # Непрочитанные сверху (под notification_inbox_idx): старое непрочитанное
        # не вытесняется за лимит новыми прочитанными, счётчик в колокольчике верный.
        inbox = self.get_queryset().order_by("is_read", "-created_at")[:INBOX_LIMIT]
        return Response(self.get_serializer(inbox, many=True).data)

    @action(detail=True, methods=["post"], url_path="read")
    def read(self, request, pk=None):
        n = self.get_object()
        n.is_read = True
        n.save(update_fields=["is_read"])
        return Response(self.get_serializer(n).data)
