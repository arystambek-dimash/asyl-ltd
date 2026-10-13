from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clients.models import Client
from apps.common.permissions import PermAPIViewMixin
from apps.common.query_params import parse_iso_date
from apps.sales.access import scope_by_client_department

from .parser import parse_act
from .services import import_act_chunk, preview_act

# Выгрузка акта 1С за год весит десятки килобайт; nginx режет /api/ на 5 МБ.
MAX_ACT_BYTES = 4 * 1024 * 1024


class ClientActImportView(PermAPIViewMixin, APIView):
    """«Импорт акта сверки» в карточке клиента.

    multipart: ``file`` — xlsx из 1С, ``start`` — «импортировать с даты»
    (ГГГГ-ММ-ДД, пусто — как предложит предпросмотр), ``preview`` —
    ``true`` (ничего не пишет) или ``false`` (следующая порция переноса).
    Права и правила — :mod:`apps.client_acts.services`.
    """

    parser_classes = [MultiPartParser]
    # Остальные права импорта (подтверждение заказов, приём оплат) проверяет сервис.
    required_perms = {"post": "orders.edit"}

    def post(self, request, pk):
        client = get_object_or_404(scope_by_client_department(Client.objects.all(), request.user), pk=pk)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"detail": "Выберите файл акта сверки (.xlsx)", "code": "act_file_required"})
        if upload.size > MAX_ACT_BYTES:
            raise ValidationError({"detail": "Файл больше 4 МБ — это не акт сверки", "code": "act_file_too_big"})
        preview = request.data.get("preview")
        if preview not in ("true", "false"):
            # Строка «да» не должна молча превратиться в запись денег.
            raise ValidationError({"detail": "Признак предпросмотра — true или false", "code": "bad_preview"})
        act = parse_act(upload.read())
        start = parse_iso_date(request.data.get("start"))
        if preview == "true":
            return Response(preview_act(client, act, request.user, start=start))
        return Response(import_act_chunk(client, act, request.user, start=start))
