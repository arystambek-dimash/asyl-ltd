from typing import ClassVar

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import SUPERUSER_ONLY, PermAPIViewMixin

from .models import DashboardSettings
from .serializers import DashboardSettingsSerializer


class DashboardSettingsView(PermAPIViewMixin, APIView):
    """Какие отделы и склады показывает «Главная»: читает её зритель, меняет суперпользователь."""

    required_perms: ClassVar[dict] = {"get": "dashboard.view", "put": SUPERUSER_ONLY}

    def get(self, request):
        return Response(DashboardSettingsSerializer(DashboardSettings.load()).data)

    def put(self, request):
        serializer = DashboardSettingsSerializer(DashboardSettings.load(), data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(DashboardSettingsSerializer(serializer.save()).data)
