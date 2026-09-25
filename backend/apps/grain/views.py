from datetime import timedelta
from typing import ClassVar
from uuid import UUID

from config.throttles import TruckScalePreviewRateThrottle
from django.conf import settings
from django.db import transaction
from django.core.cache import cache
from django.db.models import Count, F, Q, Prefetch
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cameras import ai as camera_ai
from apps.common.pagination import OptInPageNumberPagination, keyset_page
from apps.common.permissions import SUPERUSER_ONLY, IsSuperUser, PermAPIViewMixin, PermViewSetMixin
from apps.common.query_params import (
    filter_date_range,
    parse_date_range,
    parse_search_param,
    plate_search_q,
)
from apps.common.uuids import parse_canonical_uuid
from apps.common.viewsets import NoStoreMixin, SerializerViewSetMixin
from apps.eventlog.models import EventLog

from . import (
    orientation_dataset,
    passage_scale_automation,
    scale,
    services,
    vehicle_weight_capture,
    wagon_arch,
)
from . import statuses as st
from .models import (
    VEHICLE_ORIENTATION_FRONT,
    VEHICLE_ORIENTATION_REAR,
    GrainSupply,
    SiloType,
    UnassignedWeighing,
    VehicleOrientationSample,
    Wagon,
    WagonArchStop,
    WeighingPhotoDelivery,
    WeighingRecord,
)
from .scale_preview import get_scale_preview
from .queries import silo_overview
from .serializers import (
    AutomaticPassageScaleSettingsSerializer,
    GrainMovementSerializer,
    GrainSupplySerializer,
    SiloSerializer,
    SiloTypeSerializer,
    UnassignedAssignSerializer,
    HistoricalTareSerializer,
    WagonArchStopSerializer,
    WeighingRecordSerializer,
    UnassignedCreatePassageSerializer,
    UnassignedDiscardSerializer,
    UnassignedWeighingSerializer,
    VehicleOrientationSampleSerializer,
    WagonBriefSerializer,
    WagonSerializer,
)


class TruckScaleReadingView(NoStoreMixin, PermAPIViewMixin, APIView):
    """Read-only display for the Grain site's physical scale."""

    required_perms: ClassVar[dict[str, str]] = {"get": "grain.weigh"}

    def get_throttles(self):
        throttles = super().get_throttles()
        throttles.append(TruckScalePreviewRateThrottle())
        return throttles

    def get(self, request, scale_key):
        if scale_key not in scale.SCALE_KEYS:
            raise NotFound("Весовая не найдена.")
        return Response(get_scale_preview(scale_key))


class AutomaticPassageScaleAcknowledgeView(NoStoreMixin, PermAPIViewMixin, APIView):
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
        request_id = parse_canonical_uuid(request.data.get("request_id"))
        if request_id is None:
            raise ValidationError(
                {
                    "detail": "request_id должен быть canonical UUID.",
                    "code": "automatic_scale_ack_invalid",
                }
            )
        if request.data.get("resolved") is not True:
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
        return Response({"acknowledged": True, "scale_automation": runtime})


class AutomaticPassageScaleRuntimeView(NoStoreMixin, PermAPIViewMixin, APIView):
    """Permission-safe CRM projection independent of the Camera-PC."""

    required_perms: ClassVar[dict[str, str]] = {"get": "grain.view"}

    def get(self, request):
        return Response(passage_scale_automation.scale_automation_runtime())


class AutomaticPassageScaleSettingsView(NoStoreMixin, PermAPIViewMixin, APIView):
    """Expose lane timing to grain staff; mutation is superuser-only."""

    required_perms: ClassVar[dict[str, str]] = {"get": "grain.view", "patch": SUPERUSER_ONLY}

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
    value = parse_canonical_uuid(raw)
    if value is None:
        raise ValidationError(
            {
                "detail": "Idempotency-Key должен быть canonical lowercase UUID.",
                "code": "idempotency_key_invalid",
            }
        )
    return value


def _filter_wagon_search(qs, raw_search):
    """Поиск по номеру, грузу и поставщику без учёта регистра.

    Номер сверяется и с уплотнённым запросом («465 BDS 13» → 465BDS13) — тем же
    правилом, что и номер машины на доске погрузки.
    """
    search = parse_search_param(raw_search)
    if not search:
        return qs
    return qs.filter(
        plate_search_q("number", search)
        | Q(cargo_name__icontains=search)
        | Q(supply__supplier__icontains=search)
    )


def _record_stage_weight(request, wagon: Wagon, action: str) -> Wagon:
    if settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED and wagon.is_passage:
        return vehicle_weight_capture.capture_passage_weight_and_plate(
            wagon,
            action,
            request.user,
            idempotency_key=_passage_capture_idempotency_key(request),
        )
    return services.record_scale_weight(wagon, action, request.user)


class GrainSupplyViewSet(
    PermViewSetMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = (
        GrainSupply.objects.select_related("grain_type", "assigned_silo")
        .prefetch_related("wagons")
        .order_by("-id")
    )
    serializer_class = GrainSupplySerializer
    pagination_class = OptInPageNumberPagination
    required_perms = {"list": "grain.view", "create": "grain.supply"}

    def get_queryset(self):
        qs = super().get_queryset()
        status = self.request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        if self.request.query_params.get("awaiting_arrival") == "1":
            qs = qs.filter(wagons__status=st.EXPECTED).distinct()
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        supply = serializer.save(created_by=self.request.user)
        services.prepare_simple_supply(supply, self.request.user)


class GrainTripViewSet(
    SerializerViewSetMixin,
    PermViewSetMixin,
    viewsets.ReadOnlyModelViewSet,
):
    queryset = (
        Wagon.objects.select_related("supply", "assigned_silo")
        .prefetch_related(
            Prefetch("weighings", queryset=WeighingRecord.objects.select_related("operator", "reference_record")),
            "allocations__silo",
        )
        .order_by("-id")
    )
    serializer_class = WagonSerializer
    serializer_action_classes = {"list": WagonBriefSerializer}
    pagination_class = OptInPageNumberPagination
    required_perms = {
        "list": "grain.view", "retrieve": "grain.view",
        "delete_wagon": "grain.delete", "timeline": "grain.view",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        scope = params.get("scope")
        if scope == "on_site":
            qs = qs.filter(status__in=st.ON_SITE_STATUSES)
        elif scope == "finished":
            qs = qs.filter(status__in=st.FINISHED_STATUSES)
        direction = params.get("direction")
        if direction in Wagon.DIRECTIONS:
            qs = qs.filter(direction=direction)
        # День завершённого рейса — выезд; отменённый без выезда живёт на дне
        # заезда (резерв — создание записи), как ``wagonDayDate`` на фронте.
        # У остальных — заезд. Ошибки формата отдаются теми же кодами, что и
        # в журнале событий (bad_date/bad_range).
        date_from, date_to = parse_date_range(params)
        if scope == "finished":
            qs = qs.annotate(day_at=Coalesce("exited_at", "arrived_at", "created_at"))
            day_field = "day_at"
        else:
            day_field = "arrived_at"
        qs = filter_date_range(qs, day_field, date_from, date_to)
        qs = _filter_wagon_search(qs, params.get("search"))
        if scope == "finished":
            # Свежие дни сверху; внутри дня — по времени, затем по id.
            qs = qs.order_by(F("day_at").desc(nulls_last=True), "-id")
        return qs

    def _done(self, wagon: Wagon):
        wagon.refresh_from_db()
        return Response(WagonSerializer(wagon).data)

    @action(detail=True, methods=["delete"], url_path="delete")
    def delete_wagon(self, request, pk=None):
        """Удалить допустимый рейс с безопасным откатом учёта."""
        data = request.data if isinstance(request.data, dict) else {}
        result = services.delete_wagon(
            self.get_object(),
            request.user,
            reason=data.get("reason", ""),
            confirm_unrecorded_grain_handled=data.get(
                "confirm_unrecorded_grain_handled", False
            ),
        )
        return Response(result)

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


class WagonViewSet(GrainTripViewSet):
    required_perms = {
        **GrainTripViewSet.required_perms,
        "arrive": "grain.arrive",
        "entry_weight": "grain.weigh",
        "exit_weight": "grain.weigh",
        "resolve_simple_discrepancy_action": "grain.inventory",
    }

    @action(detail=False, methods=["post"], url_path="arrive")
    def arrive(self, request):
        wagon = services.register_arrival(
            request.data.get("number"),
            request.user,
            supply=_get_supply(request.data.get("supply")),
        )
        return Response(WagonSerializer(wagon).data, status=201)

    @action(detail=True, methods=["post"], url_path="entry-weight")
    def entry_weight(self, request, pk=None):
        _require_empty_scale_command(request)
        wagon = _record_stage_weight(request, self.get_object(), "entry")
        return self._done(wagon)

    @action(detail=True, methods=["post"], url_path="exit-weight")
    def exit_weight(self, request, pk=None):
        _require_empty_scale_command(request)
        wagon = _record_stage_weight(request, self.get_object(), "exit")
        return self._done(wagon)

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


class UnassignedWeighingViewSet(NoStoreMixin, PermViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Веса автовесов без номера, которые ждут привязки оператором."""

    queryset = UnassignedWeighing.objects.select_related("wagon", "resolved_by", "identity_check").order_by(
        "-id"
    )
    serializer_class = UnassignedWeighingSerializer
    pagination_class = OptInPageNumberPagination
    required_perms = {
        "list": "grain.view",
        "retrieve": "grain.view",
        "assign": "grain.weigh",
        "create_passage": "grain.weigh",
        "tare_candidates": "grain.weigh",
        "historical_exit": "grain.weigh",
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
        wagon_id = self.request.query_params.get("wagon")
        if wagon_id:
            if not wagon_id.isdecimal():
                raise ValidationError("Некорректный рейс")
            qs = qs.filter(wagon_id=int(wagon_id))
        return qs

    def _done(self, item: UnassignedWeighing):
        item.refresh_from_db()
        return Response(UnassignedWeighingSerializer(item).data)

    @action(detail=True, methods=["get"], url_path="tare-candidates")
    def tare_candidates(self, request, pk=None):
        from .historical_tare import candidates
        rows = candidates(self.get_object(), request.query_params.get("number", ""))[:20]
        return Response(WeighingRecordSerializer(rows, many=True).data)

    @action(detail=True, methods=["post"], url_path="historical-exit")
    def historical_exit(self, request, pk=None):
        from .historical_tare import complete
        serializer = HistoricalTareSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._done(complete(self.get_object(), request.user, **serializer.validated_data))

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


# Фильтры списка образцов: параметр → (поле, допустимые значения).
_ORIENTATION_SAMPLE_FILTERS = {
    "label": ("label", {VEHICLE_ORIENTATION_FRONT, VEHICLE_ORIENTATION_REAR}),
    "source": (
        "label_source",
        {
            VehicleOrientationSample.BY_TRIP,
            VehicleOrientationSample.BY_WEIGHT,
            VehicleOrientationSample.BY_MANUAL,
        },
    ),
    "kind": (
        "record_kind",
        {VehicleOrientationSample.WEIGHING, VehicleOrientationSample.UNASSIGNED},
    ),
}
# Флаги «только …»: параметр → условие при значении 1.
_ORIENTATION_SAMPLE_FLAGS = {
    "conflict": Q(conflict=True),
    "excluded": Q(excluded=True),
    "unsent": Q(sent_at__isnull=True),
}
# Без ?page плоский список ограничен, как и журнал силоса.
ORIENTATION_SAMPLE_FLAT_LIMIT = 200


ORIENTATION_PC_CACHE_KEY = "grain.orientation.camera_pc"
ORIENTATION_PC_CACHE_SECONDS = 30


def _camera_pc_orientation() -> dict | None:
    """Состояние классификатора на ПК камер для страницы датасета.

    Страница опрашивает сводку каждые 30 с из нескольких вкладок; короткий
    таймаут и кэш не дают зависшему ПК держать воркер gunicorn. Недоступный
    ПК тоже кэшируется, чтобы не стучаться на каждый опрос.
    """

    cached = cache.get(ORIENTATION_PC_CACHE_KEY)
    if cached is not None:
        return cached or None
    try:
        info = dict(camera_ai.vehicle_orientation_info() or {})
    except (camera_ai.AiUnavailable, camera_ai.AiError, ValueError, TypeError):
        info = {}
    cache.set(ORIENTATION_PC_CACHE_KEY, info, ORIENTATION_PC_CACHE_SECONDS)
    return info or None


class VehicleOrientationSampleViewSet(NoStoreMixin, viewsets.ReadOnlyModelViewSet):
    """Разметка датасета ориентации: кадры, автоматические метки, конфликты.

    Датасет — инструмент владельца: все действия только суперпользователю,
    операторам страница не показывается и API отвечает 403.

    Фильтры списка: ``label=front|rear``, ``source=trip|weight|manual``,
    ``kind=weighing|unassigned``; флаги ``conflict=1`` (только конфликты),
    ``unsent=1`` (ещё не на Camera-PC), ``excluded=1`` (только исключённые —
    по умолчанию они скрыты). Неверное значение — 400 ``bad_filter``.
    Пагинация opt-in: без ``?page``/``?page_size`` ответ — плоский список
    (не более ``ORIENTATION_SAMPLE_FLAT_LIMIT`` новых строк), с ними —
    страница ``page_size`` ≤ 100 (по умолчанию 50).
    """

    queryset = VehicleOrientationSample.objects.select_related("reviewed_by").order_by(
        "-captured_at", "-id"
    )
    serializer_class = VehicleOrientationSampleSerializer
    pagination_class = OptInPageNumberPagination
    permission_classes = [IsSuperUser]

    @staticmethod
    def _bad_filter(name: str, value: str):
        return ValidationError(
            {
                "detail": f"Недопустимое значение фильтра {name}: {value!r}.",
                "code": "bad_filter",
            }
        )

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action != "list":
            # Деталь и действия видят и исключённые строки.
            return qs
        params = self.request.query_params
        for name, (field, allowed) in _ORIENTATION_SAMPLE_FILTERS.items():
            value = params.get(name)
            if value is None:
                continue
            if value not in allowed:
                raise self._bad_filter(name, value)
            qs = qs.filter(**{field: value})
        flags = {}
        for name, condition in _ORIENTATION_SAMPLE_FLAGS.items():
            value = params.get(name, "0")
            if value not in {"0", "1"}:
                raise self._bad_filter(name, value)
            flags[name] = value == "1"
            if flags[name]:
                qs = qs.filter(condition)
        if not flags["excluded"]:
            qs = qs.filter(excluded=False)
        return qs

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = page if page is not None else list(queryset[:ORIENTATION_SAMPLE_FLAT_LIMIT])
        context = self.get_serializer_context()
        context["records"] = orientation_dataset.load_records(rows)
        data = self.get_serializer(rows, many=True, context=context).data
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    def _done(self, sample: VehicleOrientationSample):
        sample.refresh_from_db()
        return Response(self.get_serializer(sample).data)

    @action(detail=True, methods=["post"], url_path="label")
    def label(self, request, pk=None):
        """Человек сказал, как стоит машина; кадр уйдёт на Camera-PC заново."""
        label = request.data.get("label") if hasattr(request.data, "get") else None
        if label not in {VEHICLE_ORIENTATION_FRONT, VEHICLE_ORIENTATION_REAR}:
            raise ValidationError(
                {"detail": "Метка должна быть front или rear.", "code": "bad_label"}
            )
        return self._done(
            orientation_dataset.set_manual_label(self.get_object(), label, request.user)
        )

    @action(detail=True, methods=["post"], url_path="exclude")
    def exclude(self, request, pk=None):
        """Не машина или нечитаемый кадр: убрать из датасета."""
        return self._done(
            orientation_dataset.exclude_sample(self.get_object(), request.user)
        )

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """Счётчики датасета и состояние классификатора на Camera-PC.

        ``total``/``by_label``/``by_source``/``conflicts``/``unsent`` считают
        кадры в датасете (без исключённых), ``excluded`` — отдельно.
        ``camera_pc`` — null, когда ПК камер недоступен: страница не ломается.
        """
        active = Q(excluded=False)
        stats = self.get_queryset().aggregate(
            total=Count("id", filter=active),
            front=Count("id", filter=active & Q(label=VEHICLE_ORIENTATION_FRONT)),
            rear=Count("id", filter=active & Q(label=VEHICLE_ORIENTATION_REAR)),
            trip=Count(
                "id",
                filter=active & Q(label_source=VehicleOrientationSample.BY_TRIP),
            ),
            weight=Count(
                "id",
                filter=active & Q(label_source=VehicleOrientationSample.BY_WEIGHT),
            ),
            manual=Count(
                "id",
                filter=active & Q(label_source=VehicleOrientationSample.BY_MANUAL),
            ),
            conflicts=Count("id", filter=active & Q(conflict=True)),
            # Псевдоним не может совпадать с именем поля модели.
            dropped=Count("id", filter=Q(excluded=True)),
            unsent=Count("id", filter=active & Q(sent_at__isnull=True)),
        )
        return Response(
            {
                "total": stats["total"],
                "by_label": {"front": stats["front"], "rear": stats["rear"]},
                "by_source": {
                    "trip": stats["trip"],
                    "weight": stats["weight"],
                    "manual": stats["manual"],
                },
                "conflicts": stats["conflicts"],
                "excluded": stats["dropped"],
                "unsent": stats["unsent"],
                "camera_pc": _camera_pc_orientation(),
            }
        )

    @action(detail=False, methods=["post"], url_path="purge")
    def purge(self, request):
        """Стереть датасет из CRM и с Camera-PC, когда модель уже обучилась.

        Тело ``{"older_than_days": null}`` — весь датасет одним запросом к
        ПК; ``{"older_than_days": N}`` (N ≥ 1) — только кадры старше N дней,
        по одному. Ключ обязателен: «удалить всё» должно быть сказано явно.
        Один запрос стирает не больше ``PURGE_BATCH`` строк (укладываемся в
        таймаут nginx): ответ — счётчики ``purge_samples`` с ``remaining``,
        клиент повторяет запрос, пока ``remaining`` > 0 и ПК отвечает.
        ``pc_unavailable`` означает, что часть строк осталась исключёнными до
        ночного экспорта — повторять до возвращения ПК бессмысленно. Очистка
        двигает водораздел сбора: стёртый период ночью не собирается заново.
        Фото взвешиваний не трогаются. Неверное тело — 400 ``bad_purge``.
        """
        data = request.data
        if not isinstance(data, dict) or "older_than_days" not in data:
            raise ValidationError(
                {"detail": "Укажите older_than_days: null или число дней.", "code": "bad_purge"}
            )
        days = data["older_than_days"]
        if days is None:
            result = orientation_dataset.purge_all()
        else:
            if isinstance(days, bool) or not isinstance(days, int) or days < 1:
                raise ValidationError(
                    {"detail": "older_than_days должно быть null или целым ≥ 1.", "code": "bad_purge"}
                )
            cutoff = timezone.now() - timedelta(days=days)
            result = orientation_dataset.purge_samples(
                self.get_queryset().filter(captured_at__lt=cutoff), cutoff=cutoff
            )
        return Response(result)


class SiloViewSet(
    PermViewSetMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = silo_overview()
    serializer_class = SiloSerializer
    pagination_class = OptInPageNumberPagination
    http_method_names = ["get", "post", "patch", "head", "options"]
    required_perms = {
        # Чтение доступно и вкладке «Силосы» (silos.view), и зерновому процессу.
        "list": ("grain.view", "silos.view"),
        "create": "grain.admin",
        "partial_update": "grain.admin",
        "movements": ("grain.view", "silos.view"),
        "adjust": "grain.inventory",
    }

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


class SiloTypeViewSet(
    PermViewSetMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = SiloType.objects.select_related("default_silo").prefetch_related("silos")
    serializer_class = SiloTypeSerializer
    pagination_class = OptInPageNumberPagination
    http_method_names = ["get", "post", "patch", "head", "options"]
    required_perms = {
        "list": ("grain.view", "silos.view"),
        "create": ("grain.supply", "grain.admin"),
        "partial_update": "grain.admin",
    }


class WagonArchStopListView(NoStoreMixin, PermAPIViewMixin, APIView):
    required_perms = {"get": "grain.view"}

    def get(self, request):
        rows = WagonArchStop.objects.select_related("wagon").order_by("-id")
        page, next_cursor = keyset_page(rows, request.query_params.get("before"))
        deliveries = {d.request_id: d for d in WeighingPhotoDelivery.objects.filter(
            request_id__in=[stop.photo_request_id for stop in page])}
        data = WagonArchStopSerializer(page, many=True, context={"deliveries": deliveries}).data
        return Response({"results": data, "next_cursor": next_cursor})


class WagonArchStopDismissView(NoStoreMixin, PermAPIViewMixin, APIView):
    """Оператор разобрался со стопом сам — закрыть его вручную."""

    # Закрыть остановку под аркой может весовщик (grain.edit в каталоге не было).
    required_perms = {"post": "grain.weigh"}

    def post(self, request, pk):
        stop = get_object_or_404(WagonArchStop, pk=pk)
        wagon_arch.dismiss(stop, request.user)
        delivery = WeighingPhotoDelivery.objects.filter(request_id=stop.photo_request_id).first()
        data = WagonArchStopSerializer(
            stop, context={"deliveries": {stop.photo_request_id: delivery}}
        ).data
        return Response(data)
