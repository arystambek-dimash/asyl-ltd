"""Журнал WhatsApp-бота: состояние номера, сообщения, разбор и настройки.

Разбор сообщения — тот же экран, что «Вставить отчёт» у грузчика
(:mod:`apps.bots.preview`): предпросмотр, словари и «Провести» — от имени
человека, с его правами и отделом.
"""
from typing import ClassVar

from django.conf import settings
from django.db.models import Q
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.pagination import OptInPageNumberPagination
from apps.common.permissions import PermAPIViewMixin, PermViewSetMixin
from apps.common.query_params import parse_search_param
from apps.eventlog.services import log_event
from apps.orders.models import Order
from apps.sales.access import scope_by_client_department

from .models import BotMessage, WhatsAppBotSettings
from .preview import preview_report, resolution_options
from .rail import RAIL_TRANSPORT, remember_report_client, remember_report_product
from .serializers import (
    BotMessageSerializer,
    RailClientNameSerializer,
    RailProductCodeSerializer,
    RailReportSerializer,
    WhatsAppBotSettingsSerializer,
    rail_report_input,
)
from .whatsapp import EVENT_TYPE, apply_message, ignore_message, status_counts

# Короткое число — номер заказа или сообщения; длинное (номер вагона) ищется в тексте.
_SHORT_NUMBER_DIGITS = 7


def _search_q(search: str) -> Q:
    digits = search.lstrip("№#").strip()
    if digits.isdigit() and len(digits) <= _SHORT_NUMBER_DIGITS:
        return Q(order_id=int(digits)) | Q(pk=int(digits))
    return Q(text__icontains=search) | Q(sender_name__icontains=search) | Q(chat_name__icontains=search)


def _status_payload(request) -> dict:
    row = WhatsAppBotSettings.load()
    user = request.user
    return {
        # WHATSAPP_BOT_ENABLED на сервере; выключен — процесс бота простаивает.
        "server_enabled": settings.WHATSAPP_BOT_ENABLED,
        "runtime_status": row.runtime_status,
        "runtime_error": row.runtime_error,
        "polled_at": row.polled_at,
        "instance_state": row.instance_state,
        "instance_state_at": row.instance_state_at,
        "counts": status_counts(),
        "settings": WhatsAppBotSettingsSerializer(row).data,
        "can_manage": user.has_perm_code("bots.manage"),
        "can_configure": user.has_perm_code("sys_permissions.manage"),
    }


class WhatsAppBotStatusView(PermAPIViewMixin, APIView):
    """Шапка журнала: состояние номера и процесса, счётчики вкладок, настройки."""

    required_perms: ClassVar[dict] = {"get": "bots.view"}

    def get(self, request):
        return Response(_status_payload(request))


class WhatsAppBotSettingsView(PermAPIViewMixin, APIView):
    """Настройки бота меняет администратор (как настройки камер и накладной)."""

    required_perms: ClassVar[dict] = {"put": "sys_permissions.manage"}

    def put(self, request):
        row = WhatsAppBotSettings.load()
        serializer = WhatsAppBotSettingsSerializer(row, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_event(
            EVENT_TYPE,
            "Настройки WhatsApp-бота: " + ("проводит отчёты" if row.enabled else "выключен"),
            user=request.user,
            payload={key: serializer.data[key] for key in WhatsAppBotSettings.SETTINGS_FIELDS},
        )
        # Экран применяет ответ, а не перечитывает опрашиваемую шапку.
        return Response(_status_payload(request))


class BotMessageViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Сообщения бота: вкладки «На проверке / Проведено / Пропущено / Все» и разбор."""

    serializer_class = BotMessageSerializer
    pagination_class = OptInPageNumberPagination
    lookup_value_regex = r"\d+"
    required_perms: ClassVar[dict[str, str]] = {
        "list": "bots.view",
        "preview": "bots.view",
        "rail_options": "bots.view",
        "product_codes": "bots.manage",
        "client_names": "bots.manage",
        "apply": "bots.manage",
        "ignore": "bots.manage",
    }

    def get_queryset(self):
        queryset = BotMessage.objects.select_related("resolved_by")
        if self.action != "list":
            return queryset
        statuses = BotMessage.TAB_STATUSES.get(self.request.query_params.get("status") or "review")
        if statuses is not None:
            queryset = queryset.filter(status__in=statuses)
        search = parse_search_param(self.request.query_params.get("search"))
        if search:
            queryset = queryset.filter(_search_q(search))
        return queryset.order_by("-received_at", "-pk")

    def _input(self, serializer_class):
        """Текст отчёта (исправленный человеком или как пришёл) и заказ «Отгрузить по отчёту»."""
        orders = scope_by_client_department(
            Order.objects.filter(transport_type=RAIL_TRANSPORT), self.request.user, client_path="client")
        return rail_report_input(self, serializer_class, orders)

    def _preview(self, report, order):
        return Response(preview_report(report, self.request.user, order=order))

    @action(detail=True, methods=["post"], url_path="preview")
    def preview(self, request, pk=None):
        """Предпросмотр от имени человека: что проведётся и что мешает. Ничего не пишет."""
        self.get_object()
        _, report, order = self._input(RailReportSerializer)
        return self._preview(report, order)

    # Не «options»: так называется обработчик HTTP OPTIONS у DRF.
    @action(detail=True, methods=["get"], url_path="options")
    def rail_options(self, request, pk=None):
        self.get_object()
        return Response(resolution_options(request.user))

    @action(detail=True, methods=["post"], url_path="product-codes")
    def product_codes(self, request, pk=None):
        self.get_object()
        data, report, order = self._input(RailProductCodeSerializer)
        remember_report_product(data["code"], data["product"], request.user)
        return self._preview(report, order)

    @action(detail=True, methods=["post"], url_path="client-names")
    def client_names(self, request, pk=None):
        self.get_object()
        data, report, order = self._input(RailClientNameSerializer)
        remember_report_client(data["client_name"], data["client"], data["currency"], request.user)
        return self._preview(report, order)

    @action(detail=True, methods=["post"], url_path="apply")
    def apply(self, request, pk=None):
        """«Провести» от имени человека. Ответ — строка журнала: экран применяет её."""
        message = self.get_object()
        data, _, order = self._input(RailReportSerializer)
        message = apply_message(message, request.user, text=data["text"], order=order)
        return Response(self.get_serializer(message).data)

    @action(detail=True, methods=["post"], url_path="ignore")
    def ignore(self, request, pk=None):
        message = ignore_message(self.get_object(), request.user)
        return Response(self.get_serializer(message).data)
