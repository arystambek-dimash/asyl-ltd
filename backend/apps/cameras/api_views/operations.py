"""Administrative camera operations unrelated to one loading session."""

from typing import ClassVar

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasPerm, IsStaff, IsSuperUser, PermAPIViewMixin

from .. import ai, analytics, continuous, event_sync, health, production, recordings
from ..models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AlwaysOnCountArchive,
    ContinuousCameraRole,
    MonoblockCameraSettings,
)
from ..policies import (
    active_device_for,
    assert_no_pending_shipping_bootstrap,
    reserve_camera_roles,
)
from ..serializers import (
    AlwaysOnAnalyticsArchiveSerializer,
    AlwaysOnAnalyticsSubtractSerializer,
    AlwaysOnProductMappingsSerializer,
    CameraSourcesSerializer,
    ShippingBoardSettingsSerializer,
    WagonNumberCameraSettingsSerializer,
)
from ..sessions import lock_camera_binding

ALWAYS_ON_READ_PERMISSIONS = ("shipping.load", "ai_247.manage")
ALWAYS_ON_MANAGE_PERMISSION = "ai_247.manage"
SHIPPING_CONTINUOUS_READ_PERMISSIONS = (
    "shipping.load",
    "shipping.view",
    "sys_permissions.manage",
)


def _filtered_live(
    live: dict | None,
    cameras: list[str],
    analytics_scope: str | None = None,
) -> dict:
    """Return one contour's live rows without leaking the other contour."""

    camera_set = set(cameras)
    payload = dict(live or {})
    live_scopes = payload.get("analytics_scopes")
    if not isinstance(live_scopes, dict):
        live_scopes = {}
    payload["cameras"] = list(cameras)
    payload["camera_sources"] = list(cameras)
    if analytics_scope is None:
        payload["analytics_scopes"] = {
            camera: scope
            for camera, scope in live_scopes.items()
            if camera in camera_set
        }
    else:
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
            and (
                analytics_scope is None
                or item.get("analytics_scope", live_scopes.get(item.get("cam")))
                == analytics_scope
            )
        ]
    return payload


def _camera_role_conflict(cameras, occupied, *, owner: str) -> None:
    conflicts = sorted(set(cameras) & set(occupied))
    if conflicts:
        raise ValidationError(
            {
                "camera_sources": (
                    "Одна камера может иметь только один контур подсчёта. "
                    f"Уже используется в {owner}: " + ", ".join(conflicts)
                ),
                "code": "camera_role_conflict",
                "cameras": conflicts,
            }
        )


def _assert_ai247_camera(camera: str) -> str:
    camera = ai.normalize(camera)
    if camera not in MonoblockCameraSettings.ai247_sources():
        raise ValidationError(
            {
                "camera": "Камера не относится к контуру AI 24/7",
                "code": "camera_not_in_ai247",
            }
        )
    # The active list is configuration intent; the immutable reservation is
    # the ownership authority. Fail closed if a corrupted/manual DB edit makes
    # them disagree so shipping history cannot leak into AI production tools.
    return _assert_reserved_ai247_camera(camera)


def _assert_reserved_ai247_camera(camera: str) -> str:
    camera = ai.normalize(camera)
    if not ContinuousCameraRole.objects.filter(
        camera=camera,
        analytics_scope=ANALYTICS_SCOPE_AI247,
    ).exists():
        raise ValidationError(
            {
                "camera": "Камера не закреплена за контуром AI 24/7",
                "code": "camera_not_in_ai247",
            }
        )
    return camera


class _HumanAlwaysOnReadPermission(HasPerm):
    """Allow staff permissions, but never a technical MonoblockDevice account."""

    def has_permission(self, request, view):
        if getattr(request.user, "monoblock_device", None) is not None:
            return False
        return super().has_permission(request, view)


class _AlwaysOnPermissionMixin(PermAPIViewMixin):
    """Use the explicit method map and tighten read access to human users."""

    def get_permissions(self):
        method = self.request.method.lower()
        if method in {"head", "options"}:
            method = "get"
        codes = self.required_perms.get(method)
        if method != "get" or codes is None:
            return super().get_permissions()
        if isinstance(codes, str):
            codes = (codes,)
        return [_HumanAlwaysOnReadPermission(*codes)]


class AlwaysOnDetectionsView(_AlwaysOnPermissionMixin, APIView):
    """Return lightweight live detection boxes for the AI 24/7 monitor."""

    required_perms: ClassVar[dict] = {"get": ALWAYS_ON_READ_PERMISSIONS}

    def get(self, request):
        cameras = MonoblockCameraSettings.ai247_sources()
        try:
            return Response(
                _filtered_live(
                    ai.always_on_detections_cached(),
                    cameras,
                    ANALYTICS_SCOPE_AI247,
                )
            )
        except (ai.AiUnavailable, ai.AiError):
            return Response(
                {
                    "cameras": cameras,
                    "camera_sources": cameras,
                    "analytics_scopes": {
                        camera: ANALYTICS_SCOPE_AI247 for camera in cameras
                    },
                    "processors": [],
                    "pending": [],
                }
            )


class AlwaysOnCameraSettingsView(_AlwaysOnPermissionMixin, APIView):
    """Store desired 24/7 processors and synchronize them with camera-PC."""

    required_perms: ClassVar[dict] = {
        "get": ALWAYS_ON_READ_PERMISSIONS,
        "put": ALWAYS_ON_MANAGE_PERMISSION,
    }

    @staticmethod
    def _payload(row=None, live=None, sync_status="synced", detail=""):
        row = row or MonoblockCameraSettings.objects.filter(singleton=True).first()
        desired = MonoblockCameraSettings.ai247_sources(row)
        active_other = MonoblockCameraSettings.shipping_sources(row)
        blocked = MonoblockCameraSettings.reserved_sources(
            ANALYTICS_SCOPE_SHIPPING
        )
        filtered_live = _filtered_live(
            live,
            desired,
            ANALYTICS_SCOPE_AI247,
        )
        return {
            "camera_sources": desired,
            # Compatibility keys stay present but describe only this contour.
            "automatic_camera_sources": [],
            "manual_camera_sources": desired,
            "blocked_camera_sources": blocked,
            "active_other_camera_sources": active_other,
            "source": "sub",
            "analytics_scope": ANALYTICS_SCOPE_AI247,
            "processors": filtered_live.get("processors", []),
            "camera_readiness": continuous.contour_readiness(
                live or {},
                desired,
                ANALYTICS_SCOPE_AI247,
            ),
            "capacity": (live or {}).get("capacity"),
            "service_available": live is not None,
            "sync_status": sync_status,
            "detail": detail,
            "updated_at": row.updated_at if row else None,
        }

    def get(self, request):
        row = MonoblockCameraSettings.objects.filter(singleton=True).first()
        if not ai.enabled():
            return Response(
                self._payload(
                    row,
                    sync_status="pending",
                    detail="AI-сервис не настроен",
                )
            )
        try:
            live = ai.always_on_status_cached()
            desired = MonoblockCameraSettings.ai247_sources(row)
            sync_status, detail = continuous.contour_sync_state(
                live,
                desired,
                ANALYTICS_SCOPE_AI247,
            )
            return Response(
                self._payload(
                    row,
                    live,
                    sync_status,
                    detail,
                )
            )
        except (ai.AiUnavailable, ai.AiError) as exc:
            return Response(self._payload(row, sync_status="pending", detail=str(exc)))

    def put(self, request):
        serializer = CameraSourcesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sources = serializer.validated_data["camera_sources"]

        with transaction.atomic():
            # The same singleton row serializes session reservations, device
            # assignment and both camera-setting endpoints.
            lock_camera_binding()
            row = MonoblockCameraSettings.objects.select_for_update().get(
                singleton=True
            )
            shipping_sources = MonoblockCameraSettings.shipping_sources(row)
            assert_no_pending_shipping_bootstrap(sources)
            reserve_camera_roles(
                shipping_sources,
                ANALYTICS_SCOPE_SHIPPING,
            )
            reserve_camera_roles(sources, ANALYTICS_SCOPE_AI247)
            _camera_role_conflict(
                sources,
                shipping_sources,
                owner="Отгрузки",
            )
            effective_sources = MonoblockCameraSettings._ordered_camera_union(
                shipping_sources,
                sources,
            )
            previous_sources = MonoblockCameraSettings.continuous_sources(row)
            live_before = ai.cached_always_on_status() if ai.enabled() else None
            capacity = (live_before or {}).get("capacity")
            if (
                type(capacity) is int
                and capacity >= 0
                and len(effective_sources) > len(previous_sources)
                and len(effective_sources) > capacity
            ):
                raise ValidationError(
                    {
                        "camera_sources": (
                            f"ПК камер поддерживает до {capacity} активных процессоров"
                        ),
                        "code": "always_on_capacity_exceeded",
                    }
                )
            row.always_on_camera_sources = sources
            row.updated_by = request.user
            row.save(
                update_fields=[
                    "always_on_camera_sources",
                    "updated_by",
                    "updated_at",
                ]
            )
        if not ai.enabled():
            return Response(
                self._payload(
                    row,
                    sync_status="pending",
                    detail="AI-сервис не настроен",
                ),
                status=status.HTTP_202_ACCEPTED,
            )
        try:
            live = continuous.sync_always_on_policy(
                previous_sources=previous_sources,
            )
            row = MonoblockCameraSettings.objects.get(singleton=True)
            sync_status, detail = continuous.contour_sync_state(
                live,
                MonoblockCameraSettings.ai247_sources(row),
                ANALYTICS_SCOPE_AI247,
            )
            return Response(
                self._payload(row, live, sync_status, detail),
                status=(
                    status.HTTP_200_OK
                    if sync_status == "synced"
                    else status.HTTP_202_ACCEPTED
                ),
            )
        except (ai.AiUnavailable, ai.AiError) as exc:
            return Response(
                self._payload(row, sync_status="pending", detail=str(exc)),
                status=status.HTTP_202_ACCEPTED,
            )


def _shipping_visible_sources(user) -> list[str]:
    device = active_device_for(user)
    if device is not None:
        return [device.camera_source]
    return MonoblockCameraSettings.shipping_sources()


class ShippingContinuousSettingsView(APIView):
    """Read-only runtime state for the independent shipment 24/7 contour."""

    def get_permissions(self):
        return [HasPerm(*SHIPPING_CONTINUOUS_READ_PERMISSIONS)]

    @staticmethod
    def _payload(cameras, *, live=None, sync_status="synced", detail=""):
        processors = _filtered_live(
            live,
            cameras,
            ANALYTICS_SCOPE_SHIPPING,
        ).get("processors", [])
        return {
            "camera_sources": cameras,
            "blocked_camera_sources": MonoblockCameraSettings.reserved_sources(
                ANALYTICS_SCOPE_AI247
            ),
            "active_other_camera_sources": MonoblockCameraSettings.ai247_sources(),
            "source": "sub",
            "analytics_scope": ANALYTICS_SCOPE_SHIPPING,
            "processors": processors,
            "capacity": (live or {}).get("capacity"),
            "service_available": live is not None,
            "sync_status": sync_status,
            "detail": detail,
            "camera_readiness": continuous.contour_readiness(
                live or {},
                cameras,
                ANALYTICS_SCOPE_SHIPPING,
            ),
            "updated_at": (
                MonoblockCameraSettings.objects.filter(singleton=True)
                .values_list("updated_at", flat=True)
                .first()
            ),
        }

    def get(self, request):
        cameras = _shipping_visible_sources(request.user)
        if not ai.enabled():
            return Response(
                self._payload(
                    cameras,
                    sync_status="pending",
                    detail="AI-сервис не настроен",
                )
            )
        try:
            live = ai.always_on_status_cached()
            sync_status, detail = continuous.contour_sync_state(
                live,
                cameras,
                ANALYTICS_SCOPE_SHIPPING,
            )
            return Response(
                self._payload(
                    cameras,
                    live=live,
                    sync_status=sync_status,
                    detail=detail,
                )
            )
        except (ai.AiUnavailable, ai.AiError) as exc:
            return Response(
                self._payload(
                    cameras,
                    sync_status="pending",
                    detail=str(exc),
                )
            )


class ShippingContinuousDetectionsView(APIView):
    """Live boxes for shipment cameras only."""

    def get_permissions(self):
        return [HasPerm(*SHIPPING_CONTINUOUS_READ_PERMISSIONS)]

    def get(self, request):
        cameras = _shipping_visible_sources(request.user)
        try:
            return Response(
                _filtered_live(
                    ai.always_on_detections_cached(),
                    cameras,
                    ANALYTICS_SCOPE_SHIPPING,
                )
            )
        except (ai.AiUnavailable, ai.AiError):
            return Response(
                {
                    "cameras": cameras,
                    "camera_sources": cameras,
                    "analytics_scopes": {
                        camera: ANALYTICS_SCOPE_SHIPPING for camera in cameras
                    },
                    "processors": [],
                    "pending": [],
                }
            )


class ShippingContinuousAnalyticsView(APIView):
    """Operational bag analytics for shipment cameras only."""

    def get_permissions(self):
        return [HasPerm(*SHIPPING_CONTINUOUS_READ_PERMISSIONS)]

    def get(self, request):
        return Response(
            analytics.today_payload(
                ANALYTICS_SCOPE_SHIPPING,
                camera_sources=_shipping_visible_sources(request.user),
            )
        )


class WagonNumberCameraSettingsView(APIView):
    """Expose the wagon camera to grain staff; mutation is superuser-only."""

    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [HasPerm("grain.view")]
        return [IsSuperUser()]

    @staticmethod
    def _payload(row=None, live=None, sync_status="synced", detail=""):
        row = row or MonoblockCameraSettings.objects.filter(singleton=True).first()
        desired = row.wagon_number_camera_source if row else ""
        return {
            "camera_source": desired or None,
            "source": "main",
            "live": live,
            "service_available": live is not None,
            "sync_status": sync_status,
            "detail": detail,
            "updated_at": row.updated_at if row else None,
        }

    def get(self, request):
        row = MonoblockCameraSettings.objects.filter(singleton=True).first()
        if not ai.enabled():
            return Response(
                self._payload(
                    row,
                    sync_status="pending",
                    detail="AI-сервис не настроен",
                )
            )
        try:
            live = ai.wagon_number_status_cached()
            desired = row.wagon_number_camera_source if row else ""
            synced = (live.get("camera") or "") == desired
            return Response(
                self._payload(
                    row,
                    live,
                    "synced" if synced else "pending",
                    "" if synced else "Назначение ожидает синхронизации",
                )
            )
        except (ai.AiUnavailable, ai.AiError) as exc:
            return Response(self._payload(row, sync_status="pending", detail=str(exc)))

    def put(self, request):
        serializer = WagonNumberCameraSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        source = serializer.validated_data["camera_source"]

        row, _ = MonoblockCameraSettings.objects.get_or_create(singleton=True)
        row.wagon_number_camera_source = source
        row.updated_by = request.user
        row.save(
            update_fields=[
                "wagon_number_camera_source",
                "updated_by",
                "updated_at",
            ]
        )
        if not ai.enabled():
            return Response(
                self._payload(
                    row,
                    sync_status="pending",
                    detail="AI-сервис не настроен",
                ),
                status=status.HTTP_202_ACCEPTED,
            )
        try:
            live = ai.configure_wagon_number(source or None, "main")
            return Response(self._payload(row, live))
        except (ai.AiUnavailable, ai.AiError) as exc:
            return Response(
                self._payload(row, sync_status="pending", detail=str(exc)),
                status=status.HTTP_202_ACCEPTED,
            )


class AlwaysOnAnalyticsView(_AlwaysOnPermissionMixin, APIView):
    required_perms: ClassVar[dict] = {"get": ALWAYS_ON_READ_PERMISSIONS}

    def get(self, request):
        # Counting is owned by the single camera monitor.  A read request must
        # not race its event cursor or apply a cached aggregate snapshot after
        # the durable /events cutover.
        return Response(analytics.today_payload(ANALYTICS_SCOPE_AI247))


class AlwaysOnAnalyticsSubtractView(_AlwaysOnPermissionMixin, APIView):
    required_perms: ClassVar[dict] = {"post": ALWAYS_ON_MANAGE_PERMISSION}

    def post(self, request, cam: str):
        cam = _assert_ai247_camera(cam)
        serializer = AlwaysOnAnalyticsSubtractSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            analytics.subtract_today(
                cam,
                serializer.validated_data["amount"],
                serializer.validated_data["reason"],
                request.user,
                serializer.validated_data["color"],
            )
        )


class AlwaysOnAnalyticsArchiveView(_AlwaysOnPermissionMixin, APIView):
    required_perms: ClassVar[dict] = {
        "get": ALWAYS_ON_READ_PERMISSIONS,
        "post": ALWAYS_ON_MANAGE_PERMISSION,
        "delete": ALWAYS_ON_MANAGE_PERMISSION,
    }

    def get(self, request, cam: str | None = None):
        camera = cam or request.query_params.get("camera")
        if camera:
            camera = _assert_reserved_ai247_camera(camera)
        return Response(
            analytics.archives_payload(
                camera,
                camera_sources=MonoblockCameraSettings.reserved_sources(
                    ANALYTICS_SCOPE_AI247
                ),
            )
        )

    def post(self, request, cam: str):
        cam = _assert_ai247_camera(cam)
        serializer = AlwaysOnAnalyticsArchiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if ai.enabled():
            event_sync.require_fresh_drain(cam)
            try:
                sync = event_sync.sync_camera(cam)
            except (ai.AiUnavailable, ai.AiError, event_sync.EventSyncError) as exc:
                event_sync.mark_sync_failure(cam, exc)
                raise ValidationError(
                    {
                        "detail": (
                            "Архивирование отложено: журнал событий AI "
                            "ещё не синхронизирован"
                        ),
                        "code": "camera_events_not_synced",
                    }
                ) from exc
            if sync.supported and not sync.caught_up:
                raise ValidationError(
                    {
                        "detail": (
                            "Архивирование отложено: журнал событий AI ещё догружается"
                        ),
                        "code": "camera_events_not_caught_up",
                    }
                )
        return Response(
            analytics.archive_camera(
                cam,
                serializer.validated_data["note"],
                request.user,
            )
        )

    def delete(self, request, archive_id: int):
        archive_camera = (
            AlwaysOnCountArchive.objects.filter(pk=archive_id)
            .values_list("camera", flat=True)
            .first()
        )
        if archive_camera is not None:
            _assert_reserved_ai247_camera(archive_camera)
        return Response(analytics.delete_archive(archive_id, request.user))


class AlwaysOnProductionView(_AlwaysOnPermissionMixin, APIView):
    """Production periods, colour routes and scheduled warehouse receipts."""

    required_perms: ClassVar[dict] = {
        "get": ALWAYS_ON_READ_PERMISSIONS,
        "put": ALWAYS_ON_MANAGE_PERMISSION,
        "patch": ALWAYS_ON_MANAGE_PERMISSION,
    }

    def get(self, request):
        camera = request.query_params.get("camera")
        if not camera:
            raise ValidationError({"camera": "Выберите камеру"})
        camera = _assert_ai247_camera(camera)
        return Response(
            production.production_payload(
                camera,
                day=request.query_params.get("day"),
            )
        )

    def put(self, request):
        serializer = AlwaysOnProductMappingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        camera = _assert_ai247_camera(serializer.validated_data["camera"])
        return Response(
            production.save_mappings(
                camera,
                serializer.validated_data["mappings"],
                request.user,
                warehouse=serializer.validated_data.get("warehouse"),
            )
        )

    patch = put


class AlwaysOnStockRetryView(_AlwaysOnPermissionMixin, APIView):
    required_perms: ClassVar[dict] = {"post": ALWAYS_ON_MANAGE_PERMISSION}

    def post(self, request, batch_id: int):
        return Response(production.retry_batch(batch_id))


class ShippingBoardSettingsView(APIView):
    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [HasPerm("shipping.view", "sys_permissions.manage")]
        return [HasPerm("sys_permissions.manage")]

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

    put = patch


class CameraHealthView(APIView):
    permission_classes: ClassVar[list[type]] = [IsStaff]

    def get(self, request):
        payload = health.state_payload()
        http_status = (
            status.HTTP_200_OK
            if health.exit_code(payload) == 0
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return Response(payload, status=http_status)
