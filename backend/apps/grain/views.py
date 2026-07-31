from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.common.pagination import OptInPageNumberPagination
from apps.common.permissions import PermViewSetMixin
from apps.eventlog.models import EventLog

from . import services
from . import statuses as st
from .models import GrainSupply, Silo, SiloType, Wagon
from .serializers import (
    GrainMovementSerializer, GrainSupplySerializer, SiloSerializer,
    SiloTypeSerializer,
    WagonBriefSerializer, WagonSerializer,
)


def _get_supply(supply_id) -> GrainSupply | None:
    if not supply_id:
        return None
    try:
        return GrainSupply.objects.get(pk=supply_id)
    except GrainSupply.DoesNotExist:
        raise ValidationError({
            "detail": "Поставка не найдена", "code": "supply_not_found"})


class GrainSupplyViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = GrainSupply.objects.prefetch_related("wagons").order_by("-id")
    serializer_class = GrainSupplySerializer
    pagination_class = OptInPageNumberPagination
    required_perms = {
        "list": "grain.view", "retrieve": "grain.view",
        "create": "grain.supply", "update": "grain.supply",
        "partial_update": "grain.supply", "destroy": "grain.supply",
        "publish": "grain.supply", "add_wagons": "grain.supply",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        status = self.request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        return qs

    def perform_create(self, serializer):
        numbers = serializer.validated_data.pop("wagon_numbers", [])
        supply = serializer.save(created_by=self.request.user)
        services.add_wagon_numbers(supply, numbers, self.request.user)

    def perform_update(self, serializer):
        numbers = serializer.validated_data.pop("wagon_numbers", None)
        supply = serializer.save()
        if numbers:
            services.add_wagon_numbers(supply, numbers, self.request.user)

    def perform_destroy(self, instance):
        if instance.wagons.exclude(status=st.EXPECTED).exists():
            raise ValidationError({
                "detail": "Поставку с прибывшими вагонами удалить нельзя",
                "code": "supply_in_progress",
            })
        instance.wagons.all().delete()
        instance.delete()

    @action(detail=True, methods=["post"], url_path="publish")
    def publish(self, request, pk=None):
        supply = services.publish_supply(self.get_object(), request.user)
        return Response(GrainSupplySerializer(supply).data)

    @action(detail=True, methods=["post"], url_path="wagons")
    def add_wagons(self, request, pk=None):
        numbers = request.data.get("numbers") or []
        created = services.add_wagon_numbers(
            self.get_object(), numbers, request.user)
        return Response(WagonBriefSerializer(created, many=True).data,
                        status=201)


class WagonViewSet(PermViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = (Wagon.objects
                .select_related("supply", "assigned_silo")
                .prefetch_related("weighings", "lab_checks",
                                  "allocations__silo")
                .order_by("-id"))
    pagination_class = OptInPageNumberPagination
    required_perms = {
        "list": "grain.view", "retrieve": "grain.view",
        "arrive": "grain.arrive", "approve": "grain.dispatch",
        "gross": "grain.weigh", "tare": "grain.weigh",
        "lab": "grain.lab",
        "suggest_silos_action": "grain.dispatch",
        "assign_silo_action": "grain.dispatch",
        "change_silo_action": "grain.dispatch",
        "start_unloading_action": "grain.unload",
        "pause_unloading": "grain.unload",
        "finish_unloading_action": "grain.unload",
        "resolve_discrepancy_action": "grain.inventory",
        "inventory": "grain.inventory",
        "exit": "grain.exit",
        "timeline": "grain.view",
    }

    def get_serializer_class(self):
        return WagonBriefSerializer if self.action == "list" else WagonSerializer

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
        return qs

    def _done(self, wagon: Wagon):
        wagon.refresh_from_db()
        return Response(WagonSerializer(wagon).data)

    @action(detail=False, methods=["post"], url_path="arrive")
    def arrive(self, request):
        wagon = services.register_arrival(
            request.data.get("number"), request.user,
            supply=_get_supply(request.data.get("supply")),
        )
        return Response(WagonSerializer(wagon).data, status=201)

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        wagon = services.approve_unplanned(
            self.get_object(), request.user,
            supply=_get_supply(request.data.get("supply")),
        )
        return self._done(wagon)

    @action(detail=True, methods=["post"], url_path="gross")
    def gross(self, request, pk=None):
        wagon = services.record_gross(
            self.get_object(), request.data.get("weight_kg"), request.user,
            scale_number=request.data.get("scale_number") or "",
            source=request.data.get("source") or "manual",
            manual_reason=request.data.get("manual_reason") or "",
        )
        return self._done(wagon)

    @action(detail=True, methods=["post"], url_path="lab")
    def lab(self, request, pk=None):
        fields = {
            key: request.data.get(key)
            for key in ("moisture", "impurity", "nature", "grain_class",
                        "infestation", "damage", "note")
            if request.data.get(key) not in (None, "")
        }
        services.record_lab_check(
            self.get_object(), request.data.get("decision"), request.user,
            **fields)
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
            raise ValidationError({
                "detail": "Силос не найден", "code": "silo_not_found"})
        wagon = services.assign_silo(
            self.get_object(), silo, request.user,
            expected_kg=request.data.get("expected_kg"),
        )
        return self._done(wagon)

    @action(detail=True, methods=["post"], url_path="change-silo")
    def change_silo_action(self, request, pk=None):
        try:
            silo = Silo.objects.get(pk=request.data.get("silo"))
        except Silo.DoesNotExist:
            raise ValidationError({
                "detail": "Силос не найден", "code": "silo_not_found"})
        wagon = services.change_silo(
            self.get_object(), silo, request.data.get("reason") or "",
            request.user)
        return self._done(wagon)

    @action(detail=True, methods=["post"], url_path="start-unloading")
    def start_unloading_action(self, request, pk=None):
        return self._done(
            services.start_unloading(self.get_object(), request.user))

    @action(detail=True, methods=["post"], url_path="pause-unloading")
    def pause_unloading(self, request, pk=None):
        return self._done(services.set_unloading_paused(
            self.get_object(), bool(request.data.get("paused", True)),
            request.user))

    @action(detail=True, methods=["post"], url_path="finish-unloading")
    def finish_unloading_action(self, request, pk=None):
        return self._done(services.finish_unloading(
            self.get_object(), request.user,
            note=request.data.get("note") or ""))

    @action(detail=True, methods=["post"], url_path="tare")
    def tare(self, request, pk=None):
        wagon = services.record_tare(
            self.get_object(), request.data.get("weight_kg"), request.user,
            scale_number=request.data.get("scale_number") or "",
            source=request.data.get("source") or "manual",
            manual_reason=request.data.get("manual_reason") or "",
        )
        return self._done(wagon)

    @action(detail=True, methods=["post"], url_path="resolve-discrepancy")
    def resolve_discrepancy_action(self, request, pk=None):
        return self._done(services.resolve_discrepancy(
            self.get_object(), request.data.get("action") or "",
            request.user, reason=request.data.get("reason") or ""))

    @action(detail=True, methods=["post"], url_path="inventory")
    def inventory(self, request, pk=None):
        return self._done(services.inventory_wagon(
            self.get_object(), request.user,
            allocations=request.data.get("allocations")))

    @action(detail=True, methods=["post"], url_path="exit")
    def exit(self, request, pk=None):
        return self._done(services.register_exit(
            self.get_object(), request.user,
            note=request.data.get("note") or ""))

    @action(detail=True, methods=["get"], url_path="timeline")
    def timeline(self, request, pk=None):
        wagon = self.get_object()
        events = (EventLog.objects
                  .filter(event_type__startswith="grain_",
                          payload__wagon_id=wagon.pk)
                  .select_related("user")
                  .order_by("created_at")[:200])
        return Response([{
            "id": event.id,
            "event_type": event.event_type,
            "message": event.message,
            "user_name": event.user.username if event.user else None,
            "payload": event.payload,
            "created_at": event.created_at,
        } for event in events])


class SiloViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Silo.objects.select_related("silo_type").prefetch_related(
        "default_for_types")
    serializer_class = SiloSerializer
    pagination_class = OptInPageNumberPagination
    required_perms = {
        "list": "grain.view", "retrieve": "grain.view",
        "create": "grain.admin", "update": "grain.admin",
        "partial_update": "grain.admin", "destroy": "grain.admin",
        "movements": "grain.view", "adjust": "grain.inventory",
    }

    def perform_destroy(self, instance):
        if instance.movements.exists() or instance.reservations.exists():
            raise ValidationError({
                "detail": "Силос с историей удалить нельзя — заблокируйте его",
                "code": "silo_has_history",
            })
        instance.delete()

    @action(detail=True, methods=["get"], url_path="movements")
    def movements(self, request, pk=None):
        qs = self.get_object().movements.select_related(
            "wagon", "created_by").order_by("-id")
        paginator = OptInPageNumberPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        rows = GrainMovementSerializer(
            page if page is not None else qs[:200], many=True).data
        if page is not None:
            return paginator.get_paginated_response(rows)
        return Response(rows)

    @action(detail=True, methods=["post"], url_path="adjust")
    def adjust(self, request, pk=None):
        movement = services.adjust_silo(
            self.get_object(), request.data.get("delta_kg"),
            request.data.get("movement_type") or "adjustment",
            request.data.get("note") or "", request.user)
        return Response(GrainMovementSerializer(movement).data, status=201)


class SiloTypeViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = SiloType.objects.select_related("default_silo").prefetch_related(
        "silos")
    serializer_class = SiloTypeSerializer
    pagination_class = OptInPageNumberPagination
    required_perms = {
        "list": "grain.view", "retrieve": "grain.view",
        "create": "grain.admin", "update": "grain.admin",
        "partial_update": "grain.admin", "destroy": "grain.admin",
    }

    def perform_destroy(self, instance):
        if instance.silos.exists():
            raise ValidationError({
                "detail":
                    "Тип используется силосами — сначала измените их тип.",
                "code": "silo_type_in_use",
            })
        instance.delete()
