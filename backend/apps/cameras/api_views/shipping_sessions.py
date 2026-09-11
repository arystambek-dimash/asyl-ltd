"""Durable count-ledger sessions and their private per-segment evidence."""

from collections import Counter, defaultdict

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasPerm
from apps.common.query_params import filter_date_range, parse_iso_date
from apps.common.signed_media import SignedMediaView
from apps.eventlog.services import log_event
from apps.orders.models import Order
from apps.sales.access import scope_by_client_department

from .. import shipping_segments
from ..analytics import _color_payload
from ..event_protocol import event_color_key
from ..models import ShippingLoadingEvent, ShippingLoadingSegment, ShippingLoadingSession, ShippingSessionSettings

READ_PERMS = ("shipping.view", "shipping.load", "train.view", "train.load")
WRITE_PERMS = ("shipping.load", "train.load")
IMAGE_SALT = "shipping-segment-evidence-v1"
PAGE_SIZE = 20
# A conveyor needs tens of minutes per wagon, so a real day stays far below
# this; the cap only keeps one day from turning into an unbounded response.
DAY_LIMIT = 200


def _allowed(user, codes):
    return bool(user.is_active and not user.is_client and any(user.has_perm_code(code) for code in codes))


def _visible_sessions(user):
    visible_orders = scope_by_client_department(Order.objects.all(), user, client_path="client")
    rows = ShippingLoadingSession.objects.exclude(status="merged").filter(Q(order__isnull=True) | Q(order__in=visible_orders))
    kinds = []
    if _allowed(user, ("shipping.view", "shipping.load")):
        kinds.extend(("vehicle_number", ""))
    if _allowed(user, ("train.view", "train.load")):
        kinds.extend(("wagon_number", ""))
    return rows.filter(recognition_model__in=kinds)


def _visible_segments(user):
    return ShippingLoadingSegment.objects.filter(session__in=_visible_sessions(user))


def _can_identify(user, row):
    if not row.recognition_model:
        return row.identity_status == "unidentified" and _allowed(user, WRITE_PERMS)
    code = "train.load" if row.recognition_model == "wagon_number" else "shipping.load"
    return row.identity_status == "unidentified" and _allowed(user, (code,))


def segment_payload(row, user, *, order_id=None):
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
        "total_bags": row.total_bags, "photo_url": photo_url,
        "photo_taken_at": row.photo_taken_at, "idle_timeout_seconds": row.idle_timeout_seconds,
        "can_identify": _can_identify(user, row),
    }


def _session_colors(session_ids):
    """Colour mix of each session, counted from its own crossings.

    The daily analytics normaliser keeps a wagon consistent with its day. Bags
    the camera could not classify stay visible, so the parts add up to the total.
    """
    counts = defaultdict(Counter)
    rows = (
        ShippingLoadingEvent.objects.filter(segment__session_id__in=session_ids)
        .values_list("segment__session_id", "event__color", "event__class_name")
        .annotate(total=Count("pk"))
        .order_by()
    )
    for session_id, color, class_name, total in rows:
        counts[session_id][event_color_key(color, class_name) or "unclassified"] += total
    return {session_id: _color_payload(dict(colors)) for session_id, colors in counts.items()}


class ShippingSessionListView(APIView):
    def get_permissions(self):
        return [HasPerm(*READ_PERMS)]

    def get(self, request):
        rows = _visible_sessions(request.user)
        camera = request.query_params.get("camera")
        if camera:
            rows = rows.filter(camera=camera)
        parts = Prefetch("segments", queryset=ShippingLoadingSegment.objects.order_by("started_at", "id"))
        day = parse_iso_date(request.query_params.get("day"))
        next_cursor, truncated = None, False
        if day:
            # A plant day (by loading start) is one bounded list, newest first.
            found = list(filter_date_range(rows, "started_at", day, day).order_by("-started_at", "-id").prefetch_related(parts)[:DAY_LIMIT + 1])
            page, truncated = found[:DAY_LIMIT], len(found) > DAY_LIMIT
        else:
            cursor = request.query_params.get("cursor")
            if cursor:
                try:
                    value = int(cursor)
                    if value < 1:
                        raise ValueError
                except ValueError as exc:
                    raise ValidationError("Некорректная страница") from exc
                rows = rows.filter(pk__lt=value)
            # A merged group keeps its original ID. Keep every current lane visible
            # even when other cameras have produced many newer closed sessions.
            active = list(rows.filter(status="active").order_by("-last_counted_at", "-id").prefetch_related(parts)) if not cursor else []
            closed = list(rows.filter(status="closed").order_by("-id").prefetch_related(parts)[:PAGE_SIZE + 1])
            page = [*active, *closed[:PAGE_SIZE]]
            if len(closed) > PAGE_SIZE:
                next_cursor = closed[PAGE_SIZE - 1].pk
        colors = _session_colors([row.pk for row in page])
        result = []
        for row in page:
            result.append({
                "id": row.pk, "camera": row.camera, "recognition_model": row.recognition_model,
                "number": row.number, "status": row.status, "total_bags": row.total_bags,
                "started_at": row.started_at, "last_counted_at": row.last_counted_at,
                "ended_at": row.ended_at, "order_id": row.order_id, "colors": colors.get(row.pk, []),
                "segments": [segment_payload(part, request.user, order_id=row.order_id) for part in row.segments.all()],
            })
        return Response({"results": result, "next_cursor": next_cursor, "truncated": truncated})


class ShippingSegmentDetailView(APIView):
    def get_permissions(self):
        return [HasPerm(*READ_PERMS)]

    def get(self, request, pk):
        row = get_object_or_404(_visible_segments(request.user).select_related("session"), pk=pk)
        return Response(segment_payload(row, request.user, order_id=row.session.order_id))


class ShippingSegmentIdentifyView(APIView):
    def get_permissions(self):
        return [HasPerm(*WRITE_PERMS)]

    def post(self, request, pk):
        number = request.data.get("number")
        if not isinstance(number, str) or not number.strip() or len(number) > 32:
            raise ValidationError({"number": "Укажите номер машины или вагона"})
        with transaction.atomic():
            row = get_object_or_404(_visible_segments(request.user), pk=pk)
            if not _can_identify(request.user, row):
                raise PermissionDenied("Ручная привязка доступна только для неопознанного отрезка")
            if not row.recognition_model:
                kind = "wagon_number" if shipping_segments.normalized_number(number, "wagon_number") else "vehicle_number"
                code = "train.load" if kind == "wagon_number" else "shipping.load"
                if not _allowed(request.user, (code,)):
                    raise PermissionDenied("Нет права указывать номер этого типа транспорта")
            # Core locks the lane before the segment, matching projection/AI.
            try:
                shipping_segments.apply_identity(row.pk, number, "manual", user=request.user)
            except ValueError as exc:
                message = "Проверьте номер машины или восьмизначный номер вагона"
                if str(exc) == "shipping_identity_already_resolved":
                    message = "Номер уже определён. Обновите список сессий"
                raise ValidationError({"number": message}) from exc
            row.refresh_from_db()
        return Response(segment_payload(row, request.user))


class IdleSettingsSerializer(serializers.Serializer):
    idle_timeout_seconds = serializers.IntegerField(min_value=30, max_value=86400)

    def validate_idle_timeout_seconds(self, value):
        if type(self.initial_data.get("idle_timeout_seconds")) is not int:
            raise serializers.ValidationError("Укажите целое количество секунд")
        return value


class ShippingSessionSettingsView(APIView):
    def get_permissions(self):
        return [HasPerm(*(WRITE_PERMS if self.request.method == "PATCH" else READ_PERMS))]

    def get(self, request):
        row = ShippingSessionSettings.objects.filter(singleton=True).first()
        return Response({"idle_timeout_seconds": row.idle_timeout_seconds if row else 300,
                         "can_manage": _allowed(request.user, WRITE_PERMS)})

    def patch(self, request):
        serializer = IdleSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            row, _ = ShippingSessionSettings.objects.get_or_create(singleton=True)
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
        if user is None or not _allowed(user, READ_PERMS):
            raise Http404
        row = get_object_or_404(_visible_segments(user), pk=pk)
        if not row.photo:
            raise Http404
        try:
            stream = row.photo.open("rb")
        except OSError as exc:
            raise Http404 from exc
        response = FileResponse(stream, content_type="image/jpeg")
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response
