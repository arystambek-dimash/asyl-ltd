from datetime import date
from typing import ClassVar

from django.db.models import F
from django.db.models.functions import Coalesce, TruncDate
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.pagination import OptInPageNumberPagination
from apps.common.permissions import PermAPIViewMixin, PermViewSetMixin
from apps.common.query_params import parse_date_range, parse_search_param
from apps.orders.models import Order
from apps.orders.querysets import filter_order_search
from apps.sales.access import scope_by_client_department

from .models import WaybillSettings
from .serializers import (
    ArrivalSerializer,
    LoadSerializer,
    LoaderDispatchSerializer,
    LoaderOrderSerializer,
    ShipmentSerializer,
    WaybillSettingsSerializer,
)
from .services import (
    DISPATCHABLE_STATUSES,
    dispatch_order,
    loader_rollback_blocker,
    finish_loading,
    record_arrival,
    record_count,
    record_shipment,
    rewind_loading,
    rollback_shipment,
)
from .waybill import build_waybill_pdf


class ShipmentViewSet(PermViewSetMixin, viewsets.GenericViewSet):
    queryset = Order.objects.select_related("shipment").prefetch_related("items__product")
    required_perms: ClassVar[dict[str, str]] = {
        # Отгрузка — работа грузчика. Интерфейс вызывает только «Отгружено» на его
        # странице; пошаговые переходы поста остаются API под тем же правом.
        "arrive": "loader.confirm",
        "load": "loader.confirm",
        "finish_loading": "loader.confirm",
        "ship": "loader.confirm",
        "rewind_loading": "loader.confirm",
    }

    def get_queryset(self):
        qs = scope_by_client_department(
            super().get_queryset(),
            self.request.user,
            client_path="client",
        )
        return qs

    @action(detail=True, methods=["post"], url_path="arrive")
    def arrive(self, request, pk=None):
        serializer = ArrivalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = self.get_object()
        weigh_in_kg = serializer.validated_data.get("weigh_in_kg")
        shipment = record_arrival(order, weigh_in_kg, request.user)
        return Response(ShipmentSerializer(shipment).data)

    @action(detail=True, methods=["post"], url_path="load")
    def load(self, request, pk=None):
        serializer = LoadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shipment = record_count(
            self.get_object(),
            serializer.validated_data["bags"],
            request.user,
        )
        return Response(ShipmentSerializer(shipment).data)

    @action(detail=True, methods=["post"], url_path="finish-loading")
    def finish_loading(self, request, pk=None):
        shipment = finish_loading(self.get_object(), request.user)
        return Response(ShipmentSerializer(shipment).data)

    @action(detail=True, methods=["post"], url_path="rewind-loading")
    def rewind_loading(self, request, pk=None):
        order = rewind_loading(self.get_object(), request.user)
        # Клиенту достаточно нового статуса; список доски сразу перечитывается.
        return Response({"id": order.pk, "status": order.status})

    @action(detail=True, methods=["post"], url_path="ship")
    def ship(self, request, pk=None):
        shipment = record_shipment(self.get_object(), request.user)
        return Response(ShipmentSerializer(shipment).data)


class LoaderViewSet(PermViewSetMixin, viewsets.GenericViewSet):
    """Страница грузчика: очередь к отгрузке, одна кнопка «Отгружено», история и накладная."""

    serializer_class = LoaderOrderSerializer
    pagination_class = OptInPageNumberPagination
    required_perms: ClassVar[dict[str, str]] = {
        "queue": "loader.view",
        "history": "loader.view",
        "waybill": "loader.view",
        "confirm": "loader.confirm",
        "rollback": "loader.confirm",
    }

    def get_queryset(self):
        # Оплаты нужны для статуса в очереди: без prefetch это запрос на строку.
        queryset = Order.objects.select_related("client__user", "shipment").prefetch_related(
            "items__product", "payments",
        )
        return scope_by_client_department(queryset, self.request.user, client_path="client")

    def _page(self, queryset):
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(self.get_serializer(page, many=True).data)
        return Response(self.get_serializer(queryset, many=True).data)

    @action(detail=False, methods=["get"], url_path="queue")
    def queue(self, request):
        """Ждут отгрузки.

        ``day`` — плановый день (дата приезда или создания). ``overdue=1`` — только
        просроченные: их грузчик разбирает отдельно, чтобы старьё не закрывало
        сегодняшнюю работу. Без параметров — вся очередь.
        """
        today = timezone.localdate()
        queryset = self.get_queryset().filter(status__in=DISPATCHABLE_STATUSES).annotate(
            planned_on=Coalesce("arrival_date", TruncDate("created_at")),
        )
        if request.query_params.get("overdue") == "1":
            queryset = queryset.filter(planned_on__lt=today)
        raw_day = request.query_params.get("day")
        if raw_day:
            try:
                queryset = queryset.filter(planned_on=date.fromisoformat(raw_day))
            except ValueError as exc:
                raise ValidationError({"day": "Дата в формате ГГГГ-ММ-ДД"}) from exc
        queryset = filter_order_search(queryset, parse_search_param(request.query_params.get("search")))
        return self._page(queryset.order_by("planned_on", "id"))

    @action(detail=False, methods=["get"], url_path="history")
    def history(self, request):
        """Отгруженные за период по времени выезда; по умолчанию — сегодня."""
        date_from, date_to = parse_date_range(request.query_params)
        today = timezone.localdate()
        queryset = self.get_queryset().filter(
            status="shipped",
            shipment__shipped_at__date__gte=date_from or today,
            shipment__shipped_at__date__lte=date_to or date_from or today,
        )
        queryset = filter_order_search(queryset, parse_search_param(request.query_params.get("search")))
        return self._page(queryset.order_by(F("shipment__shipped_at").desc(nulls_last=True), "-id"))

    @action(detail=True, methods=["post"], url_path="dispatch")
    def confirm(self, request, pk=None):
        # Local import avoids a shipments -> cameras -> shipments import cycle.
        from apps.cameras import ai, counting

        serializer = LoaderDispatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = self.get_object()
        try:
            # Кнопок погрузки в Моноблоке нет: открытый AI-подсчёт закрывает сама отгрузка.
            counting.close_session_for_dispatch(order, request.user)
        except ai.AiUnavailable:
            return Response(
                {"detail": "AI-сервис камер недоступен — повторите отгрузку", "code": "ai_unavailable"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except ai.AiError as exc:
            return Response(
                {"detail": exc.detail, "code": "ai_error"},
                status=exc.status if exc.status in (400, 409, 503) else status.HTTP_502_BAD_GATEWAY,
            )
        dispatch_order(order, request.user, truck_number=serializer.validated_data["truck_number"])
        return Response(self.get_serializer(self.get_queryset().get(pk=pk)).data)

    @action(detail=True, methods=["post"], url_path="rollback")
    def rollback(self, request, pk=None):
        """Отменить свою отгрузку: ошибочно нажатую кнопку правит сам грузчик."""
        order = self.get_object()
        blocker = loader_rollback_blocker(order, request.user)
        if blocker:
            raise ValidationError({"detail": blocker, "code": "rollback_not_allowed"})
        reason = " ".join(str(request.data.get("reason") or "").split())
        rollback_shipment(
            order,
            request.user,
            target_status="confirmed",
            reason=reason or "Ошибочная отгрузка: отмена грузчиком",
        )
        return Response(self.get_serializer(self.get_queryset().get(pk=pk)).data)

    @action(detail=True, methods=["get"], url_path="waybill")
    def waybill(self, request, pk=None):
        order = self.get_object()
        if order.status != "shipped":
            raise ValidationError({
                "detail": "Накладная печатается после отгрузки",
                "code": "waybill_not_available",
            })
        response = HttpResponse(build_waybill_pdf(order), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="nakladnaya_{order.pk}.pdf"'
        return response


class WaybillSettingsView(PermAPIViewMixin, APIView):
    """Шапка и подписи накладной: видит грузчик, меняет администратор доступов."""

    required_perms: ClassVar[dict] = {
        "get": ("loader.view", "loader.confirm"),
        "put": "sys_permissions.manage",
    }

    def get(self, request):
        return Response(WaybillSettingsSerializer(WaybillSettings.load()).data)

    def put(self, request):
        serializer = WaybillSettingsSerializer(WaybillSettings.load(), data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
