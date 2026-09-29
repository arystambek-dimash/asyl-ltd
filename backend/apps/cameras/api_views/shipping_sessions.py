"""Durable count-ledger sessions and their private per-segment evidence."""

from typing import ClassVar

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import SUPERUSER_ONLY, PermAPIViewMixin
from apps.common.query_params import filter_date_range, parse_iso_date
from apps.common.signed_media import SignedMediaView
from apps.eventlog.services import log_event
from apps.orders.models import Order
from apps.sales.access import scope_by_client_department

from .. import shipping_segments
from ..analytics import color_payload
from ..models import ShippingLoadingSegment, ShippingLoadingSession, ShippingSessionSettings
from ..policies import MONOBLOCK_VIEW
from ..serializers import ShippingSessionColorsSerializer

IMAGE_SALT = "shipping-segment-evidence-v1"
# A conveyor needs tens of minutes per wagon, so a real day stays far below
# this; the cap only keeps one day from turning into an unbounded response.
DAY_LIMIT = 200


def _allowed(user, codes):
    return bool(user.is_active and not user.is_client and any(user.has_perm_code(code) for code in codes))


def _can_manage(user):
    return bool(user.is_active and user.is_superuser)


def _visible_sessions(user):
    visible_orders = scope_by_client_department(Order.objects.all(), user, client_path="client")
    return ShippingLoadingSession.objects.exclude(status="merged").filter(Q(order__isnull=True) | Q(order__in=visible_orders))


def _visible_segments(user):
    return ShippingLoadingSegment.objects.filter(session__in=_visible_sessions(user))


def _can_identify(user, row):
    return row.identity_status == "unidentified" and _can_manage(user)


def segment_payload(row, user, *, total_bags, order_id=None):
    """One segment; ``total_bags`` is its share of the session's shown bags."""
    photo_url = None
    if row.photo:
        token = signing.dumps({"id": row.pk, "user_id": user.pk}, salt=IMAGE_SALT)
        photo_url = f"/api/cameras/shipping-segments/{row.pk}/photo/?token={token}"
    return {
        "id": row.pk, "session_id": row.session_id, "order_id": order_id, "camera": row.camera,
        "recognition_model": row.recognition_model, "number": row.number,
        "number_source": row.number_source, "identity_status": row.identity_status,
        "identity_error": row.identity_error, "started_at": row.started_at,
        "last_counted_at": row.last_counted_at, "ended_at": row.ended_at,
        "total_bags": total_bags, "photo_url": photo_url,
        "photo_taken_at": row.photo_taken_at, "idle_timeout_seconds": row.idle_timeout_seconds,
        "can_identify": _can_identify(user, row),
    }


def _with_segments(rows):
    return rows.prefetch_related(Prefetch("segments", queryset=ShippingLoadingSegment.objects.order_by("started_at", "id")))


def _session_payloads(rows, user):
    """Sessions as every screen shows them: manual colour correction included.

    ``total_bags``/``colors`` and each segment's ``total_bags`` carry the
    correction; ``camera_colors`` keeps the camera's own count for the editor.
    """
    by_session = shipping_segments.session_colors(rows)
    result = []
    for row in rows:
        colors = by_session[row.pk]
        result.append({
            "id": row.pk, "camera": row.camera, "recognition_model": row.recognition_model,
            "number": row.number, "status": row.status, "total_bags": sum(colors.shown.values()),
            "camera_colors": colors.camera,
            "started_at": row.started_at, "last_counted_at": row.last_counted_at,
            "ended_at": row.ended_at, "order_id": row.order_id, "colors": color_payload(colors.shown),
            "segments": [
                segment_payload(part, user, total_bags=colors.segments[part.pk], order_id=row.order_id)
                for part in row.segments.all()
            ],
        })
    return result


def _segment_detail(row, user):
    """One segment with its share of its session's shown bags."""
    session = row.session
    colors = shipping_segments.session_colors([session])[session.pk]
    return segment_payload(row, user, total_bags=colors.segments[row.pk], order_id=session.order_id)


class ShippingSessionListView(PermAPIViewMixin, APIView):
    required_perms: ClassVar[dict] = {"get": MONOBLOCK_VIEW}

    def get(self, request):
        rows = _visible_sessions(request.user)
        camera = request.query_params.get("camera")
        if camera:
            rows = rows.filter(camera=camera)
        day = parse_iso_date(request.query_params.get("day"))
        if day is None:
            raise ValidationError({"detail": "Укажите день в формате ГГГГ-ММ-ДД", "code": "day_required"})
        # A plant day (by loading start) is one bounded list, newest first.
        found = list(_with_segments(filter_date_range(rows, "started_at", day, day).order_by("-started_at", "-id"))[:DAY_LIMIT + 1])
        page, truncated = found[:DAY_LIMIT], len(found) > DAY_LIMIT
        return Response({"results": _session_payloads(page, request.user), "truncated": truncated})


class ShippingSessionDetailView(PermAPIViewMixin, APIView):
    """Manual colour correction of one wagon/truck (analytics only)."""

    required_perms: ClassVar[dict] = {"patch": SUPERUSER_ONLY}

    def patch(self, request, pk):
        serializer = ShippingSessionColorsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            get_object_or_404(_visible_sessions(request.user), pk=pk)
            # Core locks the lane before the session, matching projection/AI.
            shipping_segments.set_session_colors(pk, serializer.validated_data["colors"])
            row = _with_segments(_visible_sessions(request.user)).get(pk=pk)
        return Response(_session_payloads([row], request.user)[0])


class ShippingSegmentDetailView(PermAPIViewMixin, APIView):
    required_perms: ClassVar[dict] = {"get": MONOBLOCK_VIEW}

    def get(self, request, pk):
        row = get_object_or_404(_visible_segments(request.user).select_related("session"), pk=pk)
        return Response(_segment_detail(row, request.user))


class ShippingSegmentIdentifyView(PermAPIViewMixin, APIView):
    required_perms: ClassVar[dict] = {"post": SUPERUSER_ONLY}

    def post(self, request, pk):
        number = request.data.get("number")
        if not isinstance(number, str) or not number.strip() or len(number) > 32:
            raise ValidationError({"number": "Укажите номер машины или вагона"})
        with transaction.atomic():
            row = get_object_or_404(_visible_segments(request.user), pk=pk)
            if not _can_identify(request.user, row):
                raise PermissionDenied("Ручная привязка доступна только для неопознанного отрезка")
            # Core locks the lane before the segment, matching projection/AI.
            try:
                shipping_segments.apply_identity(row.pk, number, "manual", user=request.user)
            except ValueError as exc:
                message = "Проверьте номер машины или восьмизначный номер вагона"
                if str(exc) == "shipping_identity_already_resolved":
                    message = "Номер уже определён. Обновите список сессий"
                raise ValidationError({"number": message}) from exc
            row.refresh_from_db()
        return Response(_segment_detail(row, request.user))


class IdleSettingsSerializer(serializers.Serializer):
    idle_timeout_seconds = serializers.IntegerField(min_value=30, max_value=86400)

    def validate_idle_timeout_seconds(self, value):
        if type(self.initial_data.get("idle_timeout_seconds")) is not int:
            raise serializers.ValidationError("Укажите целое количество секунд")
        return value


class ShippingSessionSettingsView(PermAPIViewMixin, APIView):
    required_perms: ClassVar[dict] = {"get": MONOBLOCK_VIEW, "patch": SUPERUSER_ONLY}

    def get(self, request):
        row = ShippingSessionSettings.objects.filter(singleton=True).first()
        return Response({"idle_timeout_seconds": row.idle_timeout_seconds if row else 300,
                         "can_manage": _can_manage(request.user)})

    def patch(self, request):
        serializer = IdleSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            row = ShippingSessionSettings.load()
            row = ShippingSessionSettings.objects.select_for_update().get(pk=row.pk)
            previous = row.idle_timeout_seconds
            row.idle_timeout_seconds = serializer.validated_data["idle_timeout_seconds"]
            row.save(update_fields=["idle_timeout_seconds"])
            log_event("shipping_idle_timeout_changed", "Изменён таймаут простоя отгрузки", user=request.user,
                      payload={"previous_seconds": previous, "seconds": row.idle_timeout_seconds})
        return Response({"idle_timeout_seconds": row.idle_timeout_seconds, "can_manage": True})


class ShippingSegmentPhotoView(SignedMediaView):
    def get(self, request, pk):
        try:
            payload = signing.loads(request.query_params.get("token", ""), salt=IMAGE_SALT, max_age=600)
        except signing.BadSignature as exc:
            raise Http404 from exc
        if not isinstance(payload, dict) or payload.get("id") != pk:
            raise Http404
        user = get_user_model().objects.filter(pk=payload.get("user_id"), is_active=True, is_client=False).first()
        if user is None or not _allowed(user, MONOBLOCK_VIEW):
            raise Http404
        row = get_object_or_404(_visible_segments(user), pk=pk)
        if not row.photo:
            raise Http404
        try:
            stream = row.photo.open("rb")
        except OSError as exc:
            raise Http404 from exc
        return self.file_response(stream)
