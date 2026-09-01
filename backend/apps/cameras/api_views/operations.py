"""Administrative camera operations unrelated to one loading session."""

from typing import ClassVar

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasPerm, IsStaff, IsSuperUser, PermAPIViewMixin

from .. import ai, analytics, continuous, event_sync, health, production, recordings
from ..models import MonoblockCameraSettings
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
        try:
            return Response(ai.always_on_detections_cached())
        except (ai.AiUnavailable, ai.AiError):
            return Response({"processors": []})


class AlwaysOnCameraSettingsView(_AlwaysOnPermissionMixin, APIView):
    """Store desired 24/7 processors and synchronize them with camera-PC."""

    required_perms: ClassVar[dict] = {
        "get": ALWAYS_ON_READ_PERMISSIONS,
        "put": ALWAYS_ON_MANAGE_PERMISSION,
    }

    @staticmethod
    def _payload(row=None, live=None, sync_status="synced", detail=""):
        row = row or MonoblockCameraSettings.objects.filter(singleton=True).first()
        automatic = MonoblockCameraSettings.mandatory_always_on_sources(row)
        desired = MonoblockCameraSettings.always_on_sources(row)
        manual = [source for source in desired if source not in set(automatic)]
        return {
            "camera_sources": desired,
            "automatic_camera_sources": automatic,
            "manual_camera_sources": manual,
            "source": "sub",
            "processors": (live or {}).get("processors", []),
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
            desired = MonoblockCameraSettings.always_on_sources(row)
            sync_status, detail = continuous.always_on_sync_state(live, desired)
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
            automatic = MonoblockCameraSettings.mandatory_always_on_sources(row)
            missing_automatic = [
                source for source in automatic if source not in sources
            ]
            if missing_automatic:
                raise ValidationError(
                    {
                        "camera_sources": (
                            "Камеры Моноблока работают 24/7 автоматически и не могут "
                            "быть отключены здесь: " + ", ".join(missing_automatic)
                        ),
                        "code": "mandatory_always_on_cameras",
                    }
                )
            automatic_set = set(automatic)
            manual_sources = [
                source for source in sources if source not in automatic_set
            ]
            effective_sources = MonoblockCameraSettings._ordered_camera_union(
                automatic,
                manual_sources,
            )
            previous_sources = MonoblockCameraSettings.always_on_sources(row)
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
            row.always_on_camera_sources = manual_sources
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
            sync_status, detail = continuous.always_on_sync_state(
                live,
                MonoblockCameraSettings.always_on_sources(row),
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
        return Response(analytics.today_payload())


class AlwaysOnAnalyticsSubtractView(_AlwaysOnPermissionMixin, APIView):
    required_perms: ClassVar[dict] = {"post": ALWAYS_ON_MANAGE_PERMISSION}

    def post(self, request, cam: str):
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
        return Response(
            analytics.archives_payload(cam or request.query_params.get("camera"))
        )

    def post(self, request, cam: str):
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
        return Response(
            production.production_payload(
                camera,
                day=request.query_params.get("day"),
            )
        )

    def put(self, request):
        serializer = AlwaysOnProductMappingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            production.save_mappings(
                serializer.validated_data["camera"],
                serializer.validated_data["mappings"],
                request.user,
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
