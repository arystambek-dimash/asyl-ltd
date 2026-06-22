import os
import time
from django.http import StreamingHttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rbac.permissions import HasPerm
from orders.models import Order
from .models import Camera, VideoJob
from .serializers import VideoJobSerializer

ALLOWED_EXT = {".mp4", ".avi", ".mov"}


class UploadVideoView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [HasPerm("shipping.load")]

    def post(self, request, order_id):
        order = Order.objects.filter(pk=order_id).first()
        if order is None:
            return Response({"detail": "Заказ не найден", "code": "order_not_found"}, status=404)
        f = request.FILES.get("video")
        if f is None:
            return Response({"detail": "Файл видео не передан", "code": "no_file"}, status=400)
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in ALLOWED_EXT:
            return Response({"detail": "Недопустимый формат видео", "code": "bad_format"}, status=400)
        camera = Camera.objects.filter(kind="counter", status="active").first()
        if camera is None:
            return Response({"detail": "Нет активной камеры-счётчика", "code": "no_counter"}, status=400)
        job = VideoJob.objects.create(order=order, camera=camera, video=f, status="queued")
        try:
            if order.status == "arrived":
                start_loading(order, request.user)
        except Exception:
            pass
        return Response(VideoJobSerializer(job).data, status=201)


from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework import viewsets, mixins
from rbac.permissions import PermViewSetMixin
from . import counter_store
from . import frame_store
from shipments.services import start_loading, record_count


def _camera_from_key(request):
    key = request.headers.get("X-Camera-Key", "")
    return Camera.objects.filter(api_key=key).first() if key else None


class VideoNextView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        cam = _camera_from_key(request)
        if cam is None:
            return Response({"detail": "Неверный ключ камеры", "code": "bad_key"}, status=401)
        with transaction.atomic():
            job = (VideoJob.objects.select_for_update(skip_locked=True)
                   .filter(status="queued", camera=cam).order_by("created_at").first())
            if job is None:
                return Response(status=204)
            job.status = "processing"
            job.started_at = timezone.now()
            job.save(update_fields=["status", "started_at"])
        url = request.build_absolute_uri(job.video.url)
        return Response({"id": job.id, "video_url": url, "camera_id": cam.camera_id})


class VideoCompleteView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, pk):
        cam = _camera_from_key(request)
        if cam is None:
            return Response({"detail": "Неверный ключ камеры", "code": "bad_key"}, status=401)
        job = VideoJob.objects.filter(pk=pk, camera=cam).first()
        if job is None:
            return Response({"detail": "Задача не найдена", "code": "not_found"}, status=404)
        bags = int(request.data.get("bags") or 0)
        by_class = request.data.get("by_class") or {}
        if not isinstance(by_class, dict):
            by_class = {}
        try:
            with transaction.atomic():
                record_count(job.order, bags, None)
                job.status = "done"
                job.bags_counted = bags
                job.counts_by_class = by_class
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "bags_counted",
                                        "counts_by_class", "finished_at"])
        except ValidationError as e:
            d = e.detail
            msg = d.get("detail") if isinstance(d, dict) else str(d)
            return Response({"detail": msg, "code": "invalid"}, status=400)
        try:
            counter_store.reset(cam.pk)
        except counter_store.CounterUnavailable:
            pass
        return Response({"status": "done", "bags": bags, "order_id": job.order_id})


class VideoFrameView(APIView):
    authentication_classes = []
    permission_classes = []
    parser_classes = []  # raw body; читаем request.body напрямую

    def post(self, request, pk):
        cam = _camera_from_key(request)
        if cam is None:
            return Response({"detail": "Неверный ключ камеры", "code": "bad_key"}, status=401)
        job = VideoJob.objects.filter(pk=pk, camera=cam).first()
        if job is None:
            return Response({"detail": "Задача не найдена", "code": "not_found"}, status=404)
        data = request.body
        if not data:
            f = request.FILES.get("frame") if hasattr(request, "FILES") else None
            data = f.read() if f else b""
        if data:
            try:
                frame_store.put(job.pk, data)
            except frame_store.FrameUnavailable:
                pass
        return Response(status=204)


class VideoStreamView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, pk):
        boundary = b"--frame"

        def gen():
            # Стрим живёт, пока задача в обработке. Жёсткий потолок итераций —
            # страховка от бесконечного цикла (1200 * 0.3с ≈ 6 минут).
            for _ in range(1200):
                job = VideoJob.objects.filter(pk=pk).only("status").first()
                if job is None or job.status != "processing":
                    break
                try:
                    frame = frame_store.get(pk)
                except frame_store.FrameUnavailable:
                    frame = None
                if frame:
                    yield (boundary + b"\r\n"
                           + b"Content-Type: image/jpeg\r\n"
                           + b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                           + frame + b"\r\n")
                time.sleep(0.3)

        resp = StreamingHttpResponse(
            gen(), content_type="multipart/x-mixed-replace; boundary=frame")
        resp["Cache-Control"] = "no-cache"
        return resp


class VideoFailView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, pk):
        cam = _camera_from_key(request)
        if cam is None:
            return Response({"detail": "Неверный ключ камеры", "code": "bad_key"}, status=401)
        job = VideoJob.objects.filter(pk=pk, camera=cam).first()
        if job is None:
            return Response({"detail": "Задача не найдена", "code": "not_found"}, status=404)
        job.status = "failed"
        job.error = str(request.data.get("error", ""))[:500]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])
        return Response({"status": "failed"})


class VideoRequeueView(APIView):
    def get_permissions(self):
        return [HasPerm("shipping.load")]

    def post(self, request, pk):
        job = VideoJob.objects.filter(pk=pk).first()
        if job is None:
            return Response({"detail": "Задача не найдена", "code": "not_found"}, status=404)
        job.status = "queued"
        job.started_at = None
        job.error = ""
        job.save(update_fields=["status", "started_at", "error"])
        return Response({"status": "queued"})


class VideoJobViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = VideoJobSerializer
    required_perms = {"list": "shipping.view"}

    def get_queryset(self):
        qs = VideoJob.objects.select_related("order")
        order = self.request.query_params.get("order")
        return qs.filter(order_id=order) if order else qs
