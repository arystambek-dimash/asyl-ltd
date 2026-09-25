"""Open AI counting sessions for the monoblock."""

from typing import ClassVar

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import PermAPIViewMixin
from apps.orders.models import Order
from apps.sales.access import scope_by_client_department

from ..models import AiCountingSession
from ..policies import MONOBLOCK_VIEW


class CameraAiSessionListView(PermAPIViewMixin, APIView):
    """Открытые отгрузки для моноблока — по одной на каждую камеру."""

    required_perms: ClassVar[dict] = {"get": MONOBLOCK_VIEW}

    def get(self, request):
        open_sessions = (
            AiCountingSession.objects.filter(
                status__in=AiCountingSession.OPEN_STATUSES,
                order__in=Order.objects.all(),
            )
            .select_related("order__client__user")
            .order_by("started_at")
        )
        open_sessions = scope_by_client_department(
            open_sessions,
            request.user,
            client_path="order__client",
        )
        return Response(
            [
                {
                    "id": session.pk,
                    "order_id": session.order_id,
                    "order_client_name": session.order.client.name,
                    "order_transport_type": session.order.transport_type,
                    "camera": session.camera,
                    "last_status": session.last_status,
                }
                for session in open_sessions
            ]
        )
