"""Administrative camera operations unrelated to one loading session."""

from typing import ClassVar

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import SUPERUSER_ONLY, PermAPIViewMixin

from .. import (
    ai,
    analytics,
    continuous,
    production,
    production_queries,
    recordings,
    shipping_history,
)
from ..models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    MonoblockCameraSettings,
)
from ..policies import (
    MONOBLOCK_SETTINGS_VIEW,
    MONOBLOCK_VIEW,
    assert_always_on_capacity,
    assert_contour_camera,
    assert_no_role_conflict,
    reserve_camera_roles,
)
from ..serializers import (
    AlwaysOnProductMappingsSerializer,
    AlwaysOnUnknownColorSerializer,
    AnalyticsRangeSerializer,
    CameraSourcesSerializer,
    ShippingBoardSettingsSerializer,
    ShippingHistorySerializer,
    WagonNumberCameraSettingsSerializer,
)
from ..sessions import lock_camera_binding


def _filtered_live(
    live: dict | None,
    cameras: list[str],
    analytics_scope: str,
) -> dict:
    """Return one contour's live rows without leaking the other contour."""

    camera_set = set(cameras)
    payload = dict(live or {})
    live_scopes = payload.get("analytics_scopes")
    if not isinstance(live_scopes, dict):
        live_scopes = {}
    payload["camera_sources"] = list(cameras)
    payload["analytics_scopes"] = {
        camera: analytics_scope
        for camera in cameras
        if live_scopes.get(camera) == analytics_scope
    }
    for key in ("processors", "pending"):
        payload[key] = [
            item
            for item in payload.get(key, [])
            if isinstance(item, dict)
            and item.get("cam") in camera_set
            and item.get("analytics_scope", live_scopes.get(item.get("cam")))
            == analytics_scope
        ]
    return payload


def _contour_payload(
    analytics_scope: str,
    row=None,
    live: dict | None = None,
    sync_status: str = "synced",
    detail: str = "",
) -> dict:
    """Settings of one contour; the other contour's cameras stay blocked."""

    other_scope = (
        ANALYTICS_SCOPE_SHIPPING
        if analytics_scope == ANALYTICS_SCOPE_AI247
        else ANALYTICS_SCOPE_AI247
    )
    desired = MonoblockCameraSettings.contour_sources(analytics_scope, row)
    return {
        "camera_sources": desired,
        "blocked_camera_sources": MonoblockCameraSettings.reserved_sources(
            other_scope
        ),
        "active_other_camera_sources": MonoblockCameraSettings.contour_sources(
            other_scope, row
        ),
        "source": "sub",
        "analytics_scope": analytics_scope,
        "processors": _filtered_live(live, desired, analytics_scope)["processors"],
        "camera_readiness": continuous.contour_readiness(
            live or {},
            desired,
            analytics_scope,
        ),
        "capacity": (live or {}).get("capacity"),
        "service_available": live is not None,
        "sync_status": sync_status,
        "detail": detail,
        "updated_at": row.updated_at if row else None,
    }


class _ContourDetectionsView(PermAPIViewMixin, APIView):
    """Lightweight live detection boxes of one contour's cameras."""

    analytics_scope: ClassVar[str]

    def get(self, request):
        try:
            live = ai.always_on_detections_cached()
        except (ai.AiUnavailable, ai.AiError):
            live = None
        return Response(
            _filtered_live(
                live,
                MonoblockCameraSettings.contour_sources(self.analytics_scope),
                self.analytics_scope,
            )
        )


class _ContourSettingsView(PermAPIViewMixin, APIView):
    """Desired cameras of one contour and their camera-PC sync state."""

    analytics_scope: ClassVar[str]

    def get(self, request):
        row = MonoblockCameraSettings.objects.filter(singleton=True).first()
        live, sync_status, detail = continuous.contour_state(
            MonoblockCameraSettings.contour_sources(self.analytics_scope, row),
            self.analytics_scope,
        )
        return Response(
            _contour_payload(self.analytics_scope, row, live, sync_status, detail)
        )


class AlwaysOnDetectionsView(_ContourDetectionsView):
    """Return lightweight live detection boxes for the AI 24/7 monitor."""

    analytics_scope = ANALYTICS_SCOPE_AI247
    required_perms: ClassVar[dict] = {"get": MONOBLOCK_VIEW}


class AlwaysOnCameraSettingsView(_ContourSettingsView):
    """Store desired 24/7 processors and synchronize them with camera-PC."""

    analytics_scope = ANALYTICS_SCOPE_AI247
    required_perms: ClassVar[dict] = {
        "get": MONOBLOCK_VIEW,
        "put": SUPERUSER_ONLY,
    }

    def put(self, request):
        serializer = CameraSourcesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sources = serializer.validated_data["camera_sources"]

        with transaction.atomic():
            # The same singleton row serializes session reservations
            # and both camera-setting endpoints.
            lock_camera_binding()
            row = MonoblockCameraSettings.objects.select_for_update().get(
                singleton=True
            )
            shipping_sources = MonoblockCameraSettings.shipping_sources(row)
            reserve_camera_roles(
                shipping_sources,
                ANALYTICS_SCOPE_SHIPPING,
            )
            reserve_camera_roles(sources, ANALYTICS_SCOPE_AI247)
            assert_no_role_conflict(
                sources,
                shipping_sources,
                owner="Отгрузки",
            )
            effective_sources = MonoblockCameraSettings.ordered_camera_union(
                shipping_sources,
                sources,
            )
            previous_sources = MonoblockCameraSettings.continuous_sources(row)
            assert_always_on_capacity(effective_sources, previous_sources)
            row.always_on_camera_sources = sources
            row.updated_by = request.user
            row.save(
                update_fields=[
                    "always_on_camera_sources",
                    "updated_by",
                    "updated_at",
                ]
            )
        live, sync_status, detail = continuous.apply_always_on_policy(
            previous_sources,
            ANALYTICS_SCOPE_AI247,
        )
        return Response(
            _contour_payload(
                ANALYTICS_SCOPE_AI247,
                MonoblockCameraSettings.objects.get(singleton=True),
                live,
                sync_status,
                detail,
            ),
            status=(
                status.HTTP_200_OK
                if sync_status == "synced"
                else status.HTTP_202_ACCEPTED
            ),
        )


class ShippingContinuousSettingsView(_ContourSettingsView):
    """Read-only runtime state for the independent shipment 24/7 contour."""

    analytics_scope = ANALYTICS_SCOPE_SHIPPING
    required_perms: ClassVar[dict] = {"get": MONOBLOCK_SETTINGS_VIEW}


class ShippingContinuousDetectionsView(_ContourDetectionsView):
    """Live boxes for shipment cameras only."""

    analytics_scope = ANALYTICS_SCOPE_SHIPPING
    required_perms: ClassVar[dict] = {"get": MONOBLOCK_SETTINGS_VIEW}


class ShippingContinuousAnalyticsView(PermAPIViewMixin, APIView):
    """Operational bag analytics for shipment cameras only."""

    required_perms: ClassVar[dict] = {"get": MONOBLOCK_SETTINGS_VIEW}

    def get(self, request):
        return Response(_analytics_payload(request, ANALYTICS_SCOPE_SHIPPING))


class ShippingContinuousHistoryView(PermAPIViewMixin, APIView):
    """Exact day periods for configured shipping cameras, without stock data."""

    required_perms: ClassVar[dict] = {"get": MONOBLOCK_SETTINGS_VIEW}

    def get(self, request):
        serializer = ShippingHistorySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        camera = assert_contour_camera(
            serializer.validated_data["camera"],
            ANALYTICS_SCOPE_SHIPPING,
            active=True,
            field="camera",
        )
        return Response(
            shipping_history.day_payload(camera, day=serializer.validated_data["day"])
        )


class WagonNumberCameraSettingsView(PermAPIViewMixin, APIView):
    """Expose the wagon camera to grain staff; mutation is superuser-only.

    Назначение живёт только в CRM: по нему poll_wagon_plate опрашивает
    /wagon-number/detect. Отдельной роли на ПК камер нет, поэтому сохранённая
    настройка сразу действует и синхронизировать её не с чем.
    """

    required_perms: ClassVar[dict] = {
        "get": ("grain.view",),
        "put": SUPERUSER_ONLY,
    }

    @staticmethod
    def _payload(row):
        return {
            "camera_source": (row.wagon_number_camera_source if row else "") or None,
            "source": "main",
            "updated_at": row.updated_at if row else None,
        }

    def get(self, request):
        row = MonoblockCameraSettings.objects.filter(singleton=True).first()
        return Response(self._payload(row))

    def put(self, request):
        serializer = WagonNumberCameraSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        source = serializer.validated_data["camera_source"]

        row = MonoblockCameraSettings.load()
        row.wagon_number_camera_source = source
        row.updated_by = request.user
        row.save(
            update_fields=[
                "wagon_number_camera_source",
                "updated_by",
                "updated_at",
            ]
        )
        return Response(self._payload(row))


class AlwaysOnAnalyticsView(PermAPIViewMixin, APIView):
    required_perms: ClassVar[dict] = {"get": MONOBLOCK_VIEW}

    def get(self, request):
        # Counting is owned by the single camera monitor.  A read request must
        # not race its event cursor.
        return Response(_analytics_payload(request, ANALYTICS_SCOPE_AI247))


class AlwaysOnProductionView(PermAPIViewMixin, APIView):
    """Production periods, colour routes and scheduled warehouse receipts."""

    required_perms: ClassVar[dict] = {
        "get": MONOBLOCK_VIEW,
        "put": SUPERUSER_ONLY,
    }

    def get(self, request):
        camera = request.query_params.get("camera")
        if not camera:
            raise ValidationError({"camera": "Выберите камеру"})
        camera = assert_contour_camera(
            camera, ANALYTICS_SCOPE_AI247, active=True, field="camera"
        )
        return Response(
            production_queries.production_payload(
                camera,
                day=request.query_params.get("day"),
            )
        )

    def put(self, request):
        serializer = AlwaysOnProductMappingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        camera = assert_contour_camera(
            serializer.validated_data["camera"],
            ANALYTICS_SCOPE_AI247,
            active=True,
            field="camera",
        )
        return Response(
            production.save_mappings(
                camera,
                serializer.validated_data["mappings"],
                request.user,
                warehouse_id=serializer.validated_data.get("warehouse"),
            )
        )


class AlwaysOnUnknownColorView(PermAPIViewMixin, APIView):
    """Assign a colour to bags the camera and the resolver left unknown."""

    required_perms: ClassVar[dict] = {"post": SUPERUSER_ONLY}

    def post(self, request):
        serializer = AlwaysOnUnknownColorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        camera = assert_contour_camera(
            data["camera"], ANALYTICS_SCOPE_AI247, active=True, field="camera"
        )
        return Response(
            production.assign_unknown_color(
                camera,
                data["business_day"],
                data["color"],
                data["bags"],
                data["reason"],
                request.user,
            )
        )


class AlwaysOnStockRetryView(PermAPIViewMixin, APIView):
    required_perms: ClassVar[dict] = {"post": SUPERUSER_ONLY}

    def post(self, request, batch_id: int):
        return Response(production.retry_batch(batch_id))


class ShippingBoardSettingsView(PermAPIViewMixin, APIView):
    required_perms: ClassVar[dict] = {
        "get": MONOBLOCK_SETTINGS_VIEW,
        "patch": ("sys_permissions.manage",),
    }

    @staticmethod
    def _payload(row=None):
        row = row or MonoblockCameraSettings.objects.filter(singleton=True).first()
        return {
            "completed_orders_days": row.completed_orders_days if row else 1,
            "video_retention_days": recordings.VIDEO_RETENTION_DAYS,
            "updated_at": row.updated_at if row else None,
        }

    def get(self, request):
        return Response(self._payload())

    def patch(self, request):
        serializer = ShippingBoardSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        days = serializer.validated_data["completed_orders_days"]
        row, _ = MonoblockCameraSettings.objects.update_or_create(
            singleton=True,
            defaults={
                "completed_orders_days": days,
                "updated_by": request.user,
            },
        )
        return Response(self._payload(row))


def _analytics_payload(request, scope):
    serializer = AnalyticsRangeSerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    params = dict(serializer.validated_data)
    camera = params.pop("camera", None)
    return analytics.today_payload(
        scope, camera_sources=[camera] if camera else None, **params
    )
