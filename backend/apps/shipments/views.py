from typing import ClassVar

from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.bots.preview import preview_report, resolution_options
from apps.bots.rail import (
    RAIL_TRANSPORT,
    apply_rail_report,
    remember_report_client,
    remember_report_product,
)
from apps.bots.serializers import (
    RailClientNameSerializer,
    RailProductCodeSerializer,
    RailReportSerializer,
    WagonReportComposeSerializer,
    WagonReportSendSerializer,
    rail_report_input,
)
from apps.bots.wagon_report import REPORT_MAX_ORDERS, report_draft, send_wagon_report, sent_payload
from apps.common.pagination import OptInPageNumberPagination
from apps.common.permissions import PermAPIViewMixin, PermViewSetMixin
from apps.common.query_params import parse_date_range, parse_iso_date, parse_search_param
from apps.orders.models import Order
from apps.orders.querysets import filter_order_search, planned_day
from apps.orders.statuses import AWAITING_SHIPMENT_STATUSES
from apps.orders.transport import suggestion_pairs
from apps.sales.access import scope_by_client_department

from .access import allowed_transports, requested_transport
from .models import WaybillSettings
from .serializers import (
    LoaderDispatchSerializer,
    LoaderOrderSerializer,
    WaybillSettingsSerializer,
)
from .services import (
    loader_dispatch,
    loader_rollback_blocker,
    rollback_shipment,
)
from .waybill import build_waybill_pdf


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
        # «Вставить отчёт» во вкладке «Вагоны»: смотреть и разбирать может
        # грузчик вагонов, провести — с правами отгрузки (и заказов для нового).
        "rail_preview": "loader.view",
        "rail_options": "loader.view",
        "rail_product_code": "loader.view",
        "rail_client_name": "loader.view",
        "rail_apply": "loader.confirm",
        # «Отправить отчёт» в истории вагонов — Динаре: составить и отправить может
        # каждый, кто видит историю вагонов (область проверяет requested_transport).
        "report_compose": "loader.view",
        "report_send": "loader.view",
    }

    def get_queryset(self):
        # Оплаты нужны для статуса в очереди, владелец номера — для
        # transport_locked: без них это запрос на строку.
        # Заказ чужой области («Фуры | Вагоны») грузчику не виден вовсе:
        # отгрузка, откат и накладная по нему — 404.
        # Плановый день — на каждой строке, в том числе в ответе действия:
        # экран по нему возвращает строку в очередь.
        queryset = Order.objects.filter(
            transport_type__in=allowed_transports(self.request.user),
        ).select_related(
            "client__user", "shipment__report_message", "truck_number_set_by",
        ).prefetch_related("items__product", "payments", "shipment__wagons").annotate(planned_on=planned_day())
        return scope_by_client_department(queryset, self.request.user, client_path="client")

    def _tab(self, queryset):
        """Вкладка «Фуры | Вагоны»: ``?transport=truck|train``, без него — все области."""
        transport = requested_transport(self.request.user, self.request.query_params.get("transport"))
        return queryset if transport is None else queryset.filter(transport_type=transport)

    def _rows(self, rows):
        # Подсказки номеров — запросом на страницу, не на строку.
        context = {**self.get_serializer_context(), "transport_pairs": suggestion_pairs(rows)}
        return self.get_serializer(rows, many=True, context=context).data

    def _page(self, queryset):
        page = self.paginate_queryset(queryset)
        data = self._rows(page if page is not None else list(queryset))
        return self.get_paginated_response(data) if page is not None else Response(data)

    def _row(self, pk):
        """Строка после действия — как в очереди, с подсказками номеров.

        Экран применяет ответ к строке, а не перечитывает список.
        """
        return Response(self._rows([self.get_queryset().get(pk=pk)])[0])

    @action(detail=False, methods=["get"], url_path="queue")
    def queue(self, request):
        """Ждут отгрузки.

        ``day`` — плановый день (дата приезда или создания). ``overdue=1`` — только
        просроченные: их грузчик разбирает отдельно, чтобы старьё не закрывало
        сегодняшнюю работу. Без параметров — вся очередь.
        """
        today = timezone.localdate()
        queryset = self._tab(self.get_queryset()).filter(status__in=AWAITING_SHIPMENT_STATUSES)
        if request.query_params.get("overdue") == "1":
            queryset = queryset.filter(planned_on__lt=today)
        day = parse_iso_date(request.query_params.get("day"))
        if day:
            queryset = queryset.filter(planned_on=day)
        queryset = filter_order_search(queryset, parse_search_param(request.query_params.get("search")))
        return self._page(queryset.order_by("planned_on", "id"))

    def _shipped_in_period(self, queryset):
        """Фильтр истории: отгруженные за период по времени выезда (по умолчанию — сегодня) и поиск."""
        params = self.request.query_params
        date_from, date_to = parse_date_range(params)
        today = timezone.localdate()
        queryset = queryset.filter(
            status="shipped",
            shipment__shipped_at__date__gte=date_from or today,
            shipment__shipped_at__date__lte=date_to or date_from or today,
        )
        return filter_order_search(queryset, parse_search_param(params.get("search")))

    @action(detail=False, methods=["get"], url_path="history")
    def history(self, request):
        """Отгруженные за период по времени выезда; по умолчанию — сегодня."""
        queryset = self._shipped_in_period(self._tab(self.get_queryset()))
        return self._page(queryset.order_by(F("shipment__shipped_at").desc(nulls_last=True), "-id"))

    def _wagon_orders(self):
        """Вагонные заказы своего отдела; без области «Вагоны» — 403, а не пустой отчёт."""
        requested_transport(self.request.user, RAIL_TRANSPORT)
        return self.get_queryset().filter(transport_type=RAIL_TRANSPORT)

    @action(detail=False, methods=["get"], url_path="wagon-report/compose")
    def report_compose(self, request):
        """«Отправить отчёт»: текст в формате владельца, кому и как он уйдёт. Ничего не пишет.

        ``?order=`` — одна отгрузка из истории; без него — вся история вагонов
        с фильтрами экрана (период и поиск).
        """
        query = WagonReportComposeSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        orders = self._wagon_orders()
        order_id = query.validated_data.get("order")
        if order_id is not None:
            rows = [get_object_or_404(orders.filter(status="shipped"), pk=order_id)]
        else:
            rows = list(self._shipped_in_period(orders).order_by("shipment__shipped_at", "id")[:REPORT_MAX_ORDERS + 1])
            if len(rows) > REPORT_MAX_ORDERS:
                raise ValidationError({
                    "detail": f"За период больше {REPORT_MAX_ORDERS} отгрузок — выберите период короче",
                    "code": "report_too_many_orders",
                })
        return Response(report_draft(rows))

    @action(detail=False, methods=["post"], url_path="wagon-report/send")
    def report_send(self, request):
        """Отправить отчёт: ботом — в очередь, ссылкой — отметить отправку. Ответ экран применяет к строкам."""
        orders = self._wagon_orders()
        serializer = WagonReportSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        ids = data["order_ids"]
        rows = list(orders.filter(status="shipped", pk__in=ids).order_by("shipment__shipped_at", "id"))
        if len(rows) != len(ids):
            raise NotFound("Заказ не найден или не отгружен")
        message = send_wagon_report(rows, data["text"], request.user, delivery=data["delivery"], key=data["key"])
        return Response(sent_payload(message))

    @action(detail=True, methods=["post"], url_path="dispatch")
    def confirm(self, request, pk=None):
        # Local import avoids a shipments -> cameras -> shipments import cycle.
        from apps.cameras import ai

        serializer = LoaderDispatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        truck = serializer.validated_data.get("truck_number", "")
        # None — прицеп не передан, «» — стереть (правила — в dispatch_order).
        trailer = serializer.validated_data.get("trailer_number")
        order = self.get_object()
        try:
            # Область и номер проверяются до закрытия AI-подсчёта, а не после него.
            loader_dispatch(order, request.user, truck_number=truck, trailer_number=trailer)
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
        return self._row(pk)

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
        return self._row(pk)

    def _rail_input(self, serializer_class):
        """Отчёт из запроса и заказ «Отгрузить по отчёту» (своей области и отдела)."""
        # Отчёт о вагонах — вкладка «Вагоны»: без этой области — 403, а не пустой разбор.
        requested_transport(self.request.user, RAIL_TRANSPORT)
        return rail_report_input(self, serializer_class, self.get_queryset())

    def _rail_preview(self, report, order):
        return Response(preview_report(report, self.request.user, order=order))

    @action(detail=False, methods=["post"], url_path="rail-report/preview")
    def rail_preview(self, request):
        """Предпросмотр отчёта о вагонах: что будет проведено и что мешает. Ничего не пишет."""
        _, report, order = self._rail_input(RailReportSerializer)
        return self._rail_preview(report, order)

    @action(detail=False, methods=["get"], url_path="rail-report/options")
    def rail_options(self, request):
        """Товары и клиенты для разрешения неизвестного — только то, что человеку можно запомнить."""
        requested_transport(request.user, RAIL_TRANSPORT)
        return Response(resolution_options(request.user))

    @action(detail=False, methods=["post"], url_path="rail-report/product-codes")
    def rail_product_code(self, request):
        """Запомнить код товара из отчёта и вернуть свежий предпросмотр."""
        data, report, order = self._rail_input(RailProductCodeSerializer)
        remember_report_product(data["code"], data["product"], request.user)
        return self._rail_preview(report, order)

    @action(detail=False, methods=["post"], url_path="rail-report/client-names")
    def rail_client_name(self, request):
        """Запомнить клиента и валюту под названием из отчёта и вернуть свежий предпросмотр."""
        data, report, order = self._rail_input(RailClientNameSerializer)
        remember_report_client(data["client_name"], data["client"], data["currency"], request.user)
        return self._rail_preview(report, order)

    @action(detail=False, methods=["post"], url_path="rail-report/apply")
    def rail_apply(self, request):
        """«Провести»: новый отчёт — заказ, подтверждение и отгрузка вагонов; с ``order`` —
        отгрузка этого заказа. Ответ — строка истории: экран применяет её, а не перечитывает."""
        _, report, order = self._rail_input(RailReportSerializer)
        order = apply_rail_report(report, request.user, order=order)
        return self._row(order.pk)

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
