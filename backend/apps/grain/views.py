from typing import ClassVar
from uuid import UUID

from config.throttles import TruckScalePreviewRateThrottle
from django.conf import settings
from django.db import transaction
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.pagination import OptInPageNumberPagination
from apps.common.permissions import IsSuperUser, PermAPIViewMixin, PermViewSetMixin
from apps.common.viewsets import SerializerViewSetMixin
from apps.eventlog.models import EventLog

from . import passage_scale_automation, scale, services, vehicle_weight_capture
from . import statuses as st
from .models import GrainSupply, Silo, SiloType, UnassignedWeighing, Wagon
from .scale_preview import get_scale_preview
from .serializers import (
    AutomaticPassageScaleSettingsSerializer,
    GrainMovementSerializer,
    GrainSupplySerializer,
    PassageNumberSerializer,
    SiloSerializer,
    SiloTypeSerializer,
    UnassignedAssignSerializer,
    UnassignedCreatePassageSerializer,
    UnassignedDiscardSerializer,
    UnassignedWeighingSerializer,
    VehiclePlateCandidateSerializer,
    WagonBriefSerializer,
    WagonSerializer,
)


class TruckScaleReadingView(PermAPIViewMixin, APIView):
    """Read-only display for the Grain site's physical scale."""

    required_perms: ClassVar[dict[str, str]] = {"get": "grain.weigh"}

    def get_throttles(self):
        throttles = super().get_throttles()
        throttles.append(TruckScalePreviewRateThrottle())
        return throttles

    def get(self, request, scale_key=scale.DEFAULT_SCALE_KEY):
        if scale_key not in scale.SCALE_KEYS:
            raise NotFound("Весовая не найдена.")
        response = Response(get_scale_preview(scale_key))
        response["Cache-Control"] = "no-store, max-age=0"
        return response


class AutomaticPassageScaleAcknowledgeView(PermAPIViewMixin, APIView):
    """Explicitly resolve a latched automatic-scale failure."""

    required_perms: ClassVar[dict[str, str]] = {"post": "grain.weigh"}

    def post(self, request):
        if not isinstance(request.data, dict) or set(request.data) != {
            "request_id",
            "resolved",
        }:
            raise ValidationError(
                {
                    "detail": "Передайте request_id и явное подтверждение resolved.",
                    "code": "automatic_scale_ack_invalid",
                }
            )
        raw_request_id = request.data.get("request_id")
        if not isinstance(raw_request_id, str):
            raise ValidationError(
                {
                    "detail": "request_id должен быть canonical UUID.",
                    "code": "automatic_scale_ack_invalid",
                }
            )
        try:
            request_id = UUID(raw_request_id)
        except ValueError as exc:
            raise ValidationError(
                {
                    "detail": "request_id должен быть canonical UUID.",
                    "code": "automatic_scale_ack_invalid",
                }
            ) from exc
        if str(request_id) != raw_request_id or request.data.get("resolved") is not True:
            raise ValidationError(
                {
                    "detail": "Подтвердите ручную обработку текущей операции.",
                    "code": "automatic_scale_ack_invalid",
                }
            )
        runtime = passage_scale_automation.acknowledge_failure(
            request_id,
            user=request.user,
        )
        response = Response(
            {"acknowledged": True, "scale_automation": runtime}
        )
        response["Cache-Control"] = "no-store"
        return response


class AutomaticPassageScaleRuntimeView(PermAPIViewMixin, APIView):
    """Permission-safe CRM projection independent of the Camera-PC."""

    required_perms: ClassVar[dict[str, str]] = {"get": "grain.view"}

    def get(self, request):
        response = Response(passage_scale_automation.scale_automation_runtime())
        response["Cache-Control"] = "no-store"
        return response


class AutomaticPassageScaleSettingsView(PermAPIViewMixin, APIView):
    """Expose lane timing to grain staff; mutation is superuser-only."""

    required_perms: ClassVar[dict[str, str]] = {"get": "grain.view"}

    def get_permissions(self):
        if self.request.method.lower() in {"patch", "put"}:
            return [IsSuperUser()]
        return super().get_permissions()

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def get(self, request):
        return Response(passage_scale_automation.scale_automation_settings())

    def patch(self, request):
        if not isinstance(request.data, dict) or set(request.data) != {
            "stable_weight_seconds"
        }:
            raise ValidationError(
                {
                    "detail": "Передайте только stable_weight_seconds.",
                    "code": "automatic_scale_settings_invalid",
                }
            )
        serializer = AutomaticPassageScaleSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            passage_scale_automation.update_scale_automation_settings(
                stable_weight_seconds=serializer.validated_data[
                    "stable_weight_seconds"
                ],
                user=request.user,
            )
        )

    put = patch


def _get_supply(supply_id) -> GrainSupply | None:
    if not supply_id:
        return None
    try:
        return GrainSupply.objects.get(pk=supply_id)
    except GrainSupply.DoesNotExist:
        raise ValidationError(
            {"detail": "Поставка не найдена", "code": "supply_not_found"}
        )


def _require_empty_scale_command(request) -> None:
    """Scale actions trust only the server-side physical scale client."""
    if not isinstance(request.data, dict) or request.data:
        raise ValidationError(
            {
                "detail": "Не передавайте вес: сервер прочитает его с весов.",
                "code": "scale_weight_server_only",
                "fields": (
                    sorted(request.data.keys())
                    if isinstance(request.data, dict)
                    else []
                ),
            }
        )


def _passage_capture_idempotency_key(request) -> UUID:
    raw = request.headers.get("Idempotency-Key", "")
    if not raw:
        raise ValidationError(
            {
                "detail": "Для фиксации веса нужен canonical UUID Idempotency-Key.",
                "code": "idempotency_key_required",
            }
        )
    try:
        value = UUID(raw)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValidationError(
            {
                "detail": "Idempotency-Key должен быть canonical lowercase UUID.",
                "code": "idempotency_key_invalid",
            }
        ) from exc
    if str(value) != raw:
        raise ValidationError(
            {
                "detail": "Idempotency-Key должен быть canonical lowercase UUID.",
                "code": "idempotency_key_invalid",
            }
        )
    return value


def _record_stage_weight(request, wagon: Wagon, action: str) -> Wagon:
    if settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED and wagon.is_passage:
        return vehicle_weight_capture.capture_passage_weight_and_plate(
            wagon,
            action,
            request.user,
            idempotency_key=_passage_capture_idempotency_key(request),
        )
    return services.record_scale_weight(wagon, action, request.user)


class GrainSupplyViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = (
        GrainSupply.objects.select_related("grain_type", "assigned_silo")
        .prefetch_related("wagons")
        .order_by("-id")
    )
    serializer_class = GrainSupplySerializer
    pagination_class = OptInPageNumberPagination
    required_perms = {
        "list": "grain.view",
        "retrieve": "grain.view",
        "create": "grain.supply",
        "update": "grain.supply",
        "partial_update": "grain.supply",
        "destroy": "grain.supply",
        "publish": "grain.supply",
        "add_wagons": "grain.supply",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        status = self.request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        if self.request.query_params.get("awaiting_arrival") == "1":
            qs = qs.filter(wagons__status=st.EXPECTED).distinct()
        return qs

    def perform_create(self, serializer):
        numbers = serializer.validated_data.pop("wagon_numbers", [])
        with transaction.atomic():
            supply = serializer.save(created_by=self.request.user)
            if supply.simple_flow:
                services.prepare_simple_supply(supply, self.request.user)
            else:
                services.add_wagon_numbers(supply, numbers, self.request.user)

    def perform_update(self, serializer):
        numbers = serializer.validated_data.pop("wagon_numbers", None)
        supply = serializer.save()
        if numbers:
            services.add_wagon_numbers(supply, numbers, self.request.user)

    def perform_destroy(self, instance):
        if instance.wagons.exclude(status=st.EXPECTED).exists():
            raise ValidationError(
                {
                    "detail": "Поставку с прибывшими вагонами удалить нельзя",
                    "code": "supply_in_progress",
                }
            )
        instance.wagons.all().delete()
        instance.delete()

    @action(detail=True, methods=["post"], url_path="publish")
    def publish(self, request, pk=None):
        supply = services.publish_supply(self.get_object(), request.user)
        return Response(GrainSupplySerializer(supply).data)

    @action(detail=True, methods=["post"], url_path="wagons")
    def add_wagons(self, request, pk=None):
        numbers = request.data.get("numbers") or []
        created = services.add_wagon_numbers(self.get_object(), numbers, request.user)
        return Response(WagonBriefSerializer(created, many=True).data, status=201)


class WagonViewSet(
    SerializerViewSetMixin,
    PermViewSetMixin,
    viewsets.ReadOnlyModelViewSet,
):
    queryset = (
        Wagon.objects.select_related("supply", "assigned_silo")
        .prefetch_related(
            "weighings",
            "lab_checks",
            "allocations__silo",
        )
        .order_by("-id")
    )
    serializer_class = WagonSerializer
    serializer_action_classes = {"list": WagonBriefSerializer}
    pagination_class = OptInPageNumberPagination
    required_perms = {
        "list": "grain.view",
        "retrieve": "grain.view",
        "arrive": "grain.arrive",
        "camera_arrive": "grain.arrive",
        "passage": "grain.arrive",
        "set_number": "grain.arrive",
        "vehicle_plate_candidates": "grain.arrive",
        "delete_wagon": "grain.delete",
        "approve": "grain.dispatch",
        "gross": "grain.weigh",
        "tare": "grain.weigh",
        "entry_weight": "grain.weigh",
        "exit_weight": "grain.weigh",
        "lab": "grain.lab",
        "suggest_silos_action": "grain.dispatch",
        "assign_silo_action": "grain.dispatch",
        "change_silo_action": "grain.dispatch",
        "start_unloading_action": "grain.unload",
        "pause_unloading": "grain.unload",
        "finish_unloading_action": "grain.unload",
        "resolve_discrepancy_action": "grain.inventory",
        "resolve_simple_discrepancy_action": "grain.inventory",
        "inventory": "grain.inventory",
        "exit": "grain.exit",
        "timeline": "grain.view",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        scope = self.request.query_params.get("scope")
        if scope == "expected":
            qs = qs.filter(status=st.EXPECTED)
        elif scope == "on_site":
            qs = qs.filter(status__in=st.ON_SITE_STATUSES)
        elif scope == "exit_ready":
            qs = qs.filter(status=st.EXIT_ALLOWED)
        elif scope == "finished":
            qs = qs.filter(status__in=st.TERMINAL_STATUSES | {st.EXITED})
        status = self.request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        direction = self.request.query_params.get("direction")
        if direction in Wagon.DIRECTIONS:
            qs = qs.filter(direction=direction)
        return qs

    def _done(self, wagon: Wagon):
        wagon.refresh_from_db()
        return Response(WagonSerializer(wagon).data)

    @action(detail=False, methods=["post"], url_path="arrive")
    def arrive(self, request):
        wagon = services.register_arrival(
            request.data.get("number"),
            request.user,
            supply=_get_supply(request.data.get("supply")),
            number_source="manual",
        )
        return Response(WagonSerializer(wagon).data, status=201)

    @action(detail=False, methods=["post"], url_path="camera-arrive")
    def camera_arrive(self, request):
        """Контракт для OCR: камера передаёт номер и ожидаемый приход."""
        supply = _get_supply(request.data.get("supply"))
        if supply is None:
            raise ValidationError(
                {
                    "detail": "Укажите ожидаемый приход",
                    "code": "supply_required",
                }
            )
        wagon = services.register_arrival(
            request.data.get("number"),
            request.user,
            supply=supply,
            number_source="camera",
            camera_source=request.data.get("camera_source") or "",
        )
        return Response(WagonSerializer(wagon).data, status=201)

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        wagon = services.approve_unplanned(
            self.get_object(),
            request.user,
            supply=_get_supply(request.data.get("supply")),
        )
        return self._done(wagon)

    @action(detail=True, methods=["post"], url_path="gross")
    def gross(self, request, pk=None):
        _require_empty_scale_command(request)
        wagon = services.record_scale_weight(
            self.get_object(), "gross", request.user
        )
        return self._done(wagon)

    @action(detail=True, methods=["delete"], url_path="delete")
    def delete_wagon(self, request, pk=None):
        """Удалить допустимый рейс с безопасным откатом учёта."""
        if hasattr(request.data, "__contains__") and "reason" in request.data:
            reason = request.data.get("reason")
        else:
            # Transitional fallback for callers using the previous contract.
            reason = request.query_params.get("reason") or ""
        result = services.delete_wagon(
            self.get_object(),
            request.user,
            reason=reason,
            confirm_unrecorded_grain_handled=(
                request.data.get("confirm_unrecorded_grain_handled", False)
                if hasattr(request.data, "get")
                else False
            ),
        )
        return Response(result)

    @action(detail=False, methods=["post"], url_path="passage")
    def passage(self, request):
        """Регистрация прохода: машина заехала за отрубями."""
        wagon = services.create_passage(
            request.user,
            number=request.data.get("number") or "",
            cargo_name=request.data.get("cargo_name") or "",
            note=request.data.get("note") or "",
            vehicle_plate_event_id=request.data.get("vehicle_plate_event_id"),
        )
        return Response(WagonSerializer(wagon).data, status=201)

    @action(detail=True, methods=["patch", "post"], url_path="number")
    def set_number(self, request, pk=None):
        """Дописать или поправить номер машины, который камера не прочла."""
        serializer = PassageNumberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wagon = services.set_passage_number(
            self.get_object(),
            serializer.validated_data["number"],
            request.user,
        )
        return self._done(wagon)

    @action(
        detail=False,
        methods=["get"],
        url_path="vehicle-plate-candidates",
    )
    def vehicle_plate_candidates(self, request):
        events = services.vehicle_plate_candidates()
        response = Response(VehiclePlateCandidateSerializer(events, many=True).data)
        response["Cache-Control"] = "no-store"
        return response

    @action(detail=True, methods=["post"], url_path="entry-weight")
    def entry_weight(self, request, pk=None):
        _require_empty_scale_command(request)
        wagon = _record_stage_weight(request, self.get_object(), "entry")
        return self._done(wagon)

    @action(detail=True, methods=["post"], url_path="lab")
    def lab(self, request, pk=None):
        fields = {
            key: request.data.get(key)
            for key in (
                "moisture",
                "impurity",
                "nature",
                "grain_class",
                "infestation",
                "damage",
                "note",
            )
            if request.data.get(key) not in (None, "")
        }
        services.record_lab_check(
            self.get_object(), request.data.get("decision"), request.user, **fields
        )
        return self._done(self.get_object())

    @action(detail=True, methods=["get"], url_path="suggest-silos")
    def suggest_silos_action(self, request, pk=None):
        silos = services.suggest_silos(self.get_object())
        return Response(SiloSerializer(silos, many=True).data)

    @action(detail=True, methods=["post"], url_path="assign-silo")
    def assign_silo_action(self, request, pk=None):
        try:
            silo = Silo.objects.get(pk=request.data.get("silo"))
        except Silo.DoesNotExist:
            raise ValidationError(
                {"detail": "Силос не найден", "code": "silo_not_found"}
            )
        wagon = services.assign_silo(
            self.get_object(),
            silo,
            request.user,
            expected_kg=request.data.get("expected_kg"),
        )
        return self._done(wagon)

    @action(detail=True, methods=["post"], url_path="change-silo")
    def change_silo_action(self, request, pk=None):
        try:
            silo = Silo.objects.get(pk=request.data.get("silo"))
        except Silo.DoesNotExist:
            raise ValidationError(
                {"detail": "Силос не найден", "code": "silo_not_found"}
            )
        wagon = services.change_silo(
            self.get_object(), silo, request.data.get("reason") or "", request.user
        )
        return self._done(wagon)

    @action(detail=True, methods=["post"], url_path="start-unloading")
    def start_unloading_action(self, request, pk=None):
        return self._done(services.start_unloading(self.get_object(), request.user))

    @action(detail=True, methods=["post"], url_path="pause-unloading")
    def pause_unloading(self, request, pk=None):
        return self._done(
            services.set_unloading_paused(
                self.get_object(), bool(request.data.get("paused", True)), request.user
            )
        )

    @action(detail=True, methods=["post"], url_path="finish-unloading")
    def finish_unloading_action(self, request, pk=None):
        return self._done(
            services.finish_unloading(
                self.get_object(), request.user, note=request.data.get("note") or ""
            )
        )

    @action(detail=True, methods=["post"], url_path="tare")
    def tare(self, request, pk=None):
        _require_empty_scale_command(request)
        wagon = services.record_scale_weight(
            self.get_object(), "tare", request.user
        )
        return self._done(wagon)

    @action(detail=True, methods=["post"], url_path="exit-weight")
    def exit_weight(self, request, pk=None):
        _require_empty_scale_command(request)
        wagon = _record_stage_weight(request, self.get_object(), "exit")
        return self._done(wagon)

    @action(detail=True, methods=["post"], url_path="resolve-discrepancy")
    def resolve_discrepancy_action(self, request, pk=None):
        return self._done(
            services.resolve_discrepancy(
                self.get_object(),
                request.data.get("action") or "",
                request.user,
                reason=request.data.get("reason") or "",
            )
        )

    @action(detail=True, methods=["post"], url_path="resolve-simple-discrepancy")
    def resolve_simple_discrepancy_action(self, request, pk=None):
        return self._done(
            services.resolve_simple_discrepancy(
                self.get_object(),
                request.data.get("action") or "",
                request.user,
                reason=request.data.get("reason") or "",
            )
        )

    @action(detail=True, methods=["post"], url_path="inventory")
    def inventory(self, request, pk=None):
        return self._done(
            services.inventory_wagon(
                self.get_object(),
                request.user,
                allocations=request.data.get("allocations"),
            )
        )

    @action(detail=True, methods=["post"], url_path="exit")
    def exit(self, request, pk=None):
        return self._done(
            services.register_exit(
                self.get_object(), request.user, note=request.data.get("note") or ""
            )
        )

    @action(detail=True, methods=["get"], url_path="timeline")
    def timeline(self, request, pk=None):
        wagon = self.get_object()
        events = (
            EventLog.objects.filter(
                event_type__startswith="grain_", payload__wagon_id=wagon.pk
            )
            .select_related("user")
            .order_by("created_at")[:200]
        )
        return Response(
            [
                {
                    "id": event.id,
                    "event_type": event.event_type,
                    "message": event.message,
                    "user_name": event.user.username if event.user else None,
                    "payload": event.payload,
                    "created_at": event.created_at,
                }
                for event in events
            ]
        )


class UnassignedWeighingViewSet(PermViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Веса автовесов без номера, которые ждут привязки оператором."""

    queryset = UnassignedWeighing.objects.select_related("wagon", "resolved_by").order_by(
        "-id"
    )
    serializer_class = UnassignedWeighingSerializer
    pagination_class = OptInPageNumberPagination
    required_perms = {
        "list": "grain.view",
        "retrieve": "grain.view",
        "assign": "grain.weigh",
        "create_passage": "grain.weigh",
        "discard": "grain.weigh",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action != "list":
            # Detail routes must still resolve rows that just left "open".
            return qs
        status = self.request.query_params.get("status", UnassignedWeighing.OPEN)
        if status != "all":
            qs = qs.filter(status=status)
        return qs

    def _done(self, item: UnassignedWeighing):
        item.refresh_from_db()
        response = Response(UnassignedWeighingSerializer(item).data)
        response["Cache-Control"] = "no-store"
        return response

    @action(detail=True, methods=["post"], url_path="assign")
    def assign(self, request, pk=None):
        serializer = UnassignedAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wagon = Wagon.objects.filter(pk=serializer.validated_data["wagon"]).first()
        if wagon is None:
            raise NotFound("Рейс не найден")
        return self._done(
            services.assign_unassigned_weighing(self.get_object(), wagon, request.user)
        )

    @action(detail=True, methods=["post"], url_path="create-passage")
    def create_passage(self, request, pk=None):
        serializer = UnassignedCreatePassageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._done(
            services.create_passage_from_unassigned_weighing(
                self.get_object(),
                request.user,
                number=serializer.validated_data["number"],
                cargo_name=serializer.validated_data["cargo_name"],
            )
        )

    @action(detail=True, methods=["post"], url_path="discard")
    def discard(self, request, pk=None):
        serializer = UnassignedDiscardSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._done(
            services.discard_unassigned_weighing(
                self.get_object(),
                request.user,
                reason=serializer.validated_data["reason"],
            )
        )


class SiloViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Silo.objects.select_related("silo_type").prefetch_related(
        "default_for_types"
    )
    serializer_class = SiloSerializer
    pagination_class = OptInPageNumberPagination
    required_perms = {
        # Чтение доступно и вкладке «Силосы» (silos.view), и зерновому процессу.
        "list": ("grain.view", "silos.view"),
        "retrieve": ("grain.view", "silos.view"),
        "create": "grain.admin",
        "update": "grain.admin",
        "partial_update": "grain.admin",
        "destroy": "grain.admin",
        "movements": ("grain.view", "silos.view"),
        "adjust": "grain.inventory",
    }

    def perform_destroy(self, instance):
        if instance.movements.exists() or instance.reservations.exists():
            raise ValidationError(
                {
                    "detail": "Силос с историей удалить нельзя — заблокируйте его",
                    "code": "silo_has_history",
                }
            )
        instance.delete()

    @action(detail=True, methods=["get"], url_path="movements")
    def movements(self, request, pk=None):
        qs = (
            self.get_object()
            .movements.select_related("wagon", "created_by")
            .order_by("-id")
        )
        paginator = OptInPageNumberPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        rows = GrainMovementSerializer(
            page if page is not None else qs[:200], many=True
        ).data
        if page is not None:
            return paginator.get_paginated_response(rows)
        return Response(rows)

    @action(detail=True, methods=["post"], url_path="adjust")
    def adjust(self, request, pk=None):
        movement = services.adjust_silo(
            self.get_object(),
            request.data.get("delta_kg"),
            request.data.get("movement_type") or "adjustment",
            request.data.get("note") or "",
            request.user,
        )
        return Response(GrainMovementSerializer(movement).data, status=201)


class SiloTypeViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = SiloType.objects.select_related("default_silo").prefetch_related("silos")
    serializer_class = SiloTypeSerializer
    pagination_class = OptInPageNumberPagination
    required_perms = {
        "list": ("grain.view", "silos.view"),
        "retrieve": ("grain.view", "silos.view"),
        "create": ("grain.supply", "grain.admin"),
        "update": "grain.admin",
        "partial_update": "grain.admin",
        "destroy": "grain.admin",
    }

    def perform_destroy(self, instance):
        if instance.silos.exists():
            raise ValidationError(
                {
                    "detail": "Тип используется силосами — сначала измените их тип.",
                    "code": "silo_type_in_use",
                }
            )
        instance.delete()
