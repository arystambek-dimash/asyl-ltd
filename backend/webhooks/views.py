from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import viewsets, mixins
from rest_framework.decorators import action
from rbac.permissions import PermViewSetMixin
from .models import Camera, WebhookCall
from .serializers import CameraSerializer, WebhookCallSerializer
from .services import normalize_plate, _build_context, _find_order
from .templating import render_template


class CameraWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        from .services import process_webhook
        camera_id = request.data.get("camera_id")
        camera = Camera.objects.filter(camera_id=camera_id).first()
        if camera is None:
            return Response({"detail": "Камера не найдена", "code": "camera_not_found"}, status=404)
        key = request.headers.get("X-Camera-Key", "")
        if key != camera.api_key:
            return Response({"detail": "Неверный ключ камеры", "code": "bad_key"}, status=401)
        if not camera.is_active:
            return Response({"detail": "Камера отключена", "code": "camera_inactive"}, status=403)
        return Response(process_webhook(camera, request.data), status=200)


def _dry_run(kind, order, bags, weight):
    """Проверка без побочных эффектов — зеркалит status-гарды сервисов."""
    if kind == "entry":
        if order.status not in ("confirmed", "paid"):
            return "deny", "Машину можно принять только для подтверждённого заказа"
        if not order.is_fully_paid:
            return "deny", "Заказ не оплачен — въезд запрещён"
        return "allow", ""
    if kind == "counter":
        if order.status != "arrived":
            return "deny", "Загрузка возможна только после прибытия"
        return "allow", ""
    if kind == "exit":
        if order.status != "loading":
            return "deny", "Выезд возможен только во время загрузки"
        return "allow", ""
    return "deny", "Неизвестный тип камеры"


class CameraViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Camera.objects.all()
    serializer_class = CameraSerializer
    required_perms = {
        "list": "cameras.view", "retrieve": "cameras.view", "calls": "cameras.view",
        "create": "cameras.manage", "update": "cameras.manage",
        "partial_update": "cameras.manage", "destroy": "cameras.manage",
        "regenerate_key": "cameras.manage", "simulate": "cameras.manage",
    }

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cam = ser.save()
        return Response(CameraSerializer(cam, context={"reveal_key": True}).data, status=201)

    @action(detail=True, methods=["post"], url_path="regenerate_key")
    def regenerate_key(self, request, pk=None):
        cam = self.get_object()
        cam.api_key = Camera.generate_key()
        cam.save(update_fields=["api_key"])
        return Response(CameraSerializer(cam, context={"reveal_key": True}).data)

    @action(detail=True, methods=["post"])
    def simulate(self, request, pk=None):
        cam = self.get_object()
        plate = normalize_plate(request.data.get("plate", ""))
        bags = request.data.get("bags")
        weight = request.data.get("weight_kg")
        order = _find_order(plate)
        decision, reason = "deny", ""
        if order is None:
            reason = "Заказ по номеру не найден"
        else:
            decision, reason = _dry_run(cam.kind, order, bags, weight)
        ctx = _build_context(cam, plate, order, decision, reason, bags, weight)
        try:
            response = render_template(cam.response_template, ctx)
        except ValueError:
            response = render_template("", ctx)
        return Response({"response": response, "decision": decision,
                         "reason": reason, "order_id": order.id if order else None})

    @action(detail=True, methods=["get"])
    def calls(self, request, pk=None):
        qs = self.get_object().calls.all()[:50]
        return Response(WebhookCallSerializer(qs, many=True).data)


class WebhookCallViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = WebhookCallSerializer
    required_perms = {"list": "cameras.view"}

    def get_queryset(self):
        qs = WebhookCall.objects.select_related("camera")
        cam = self.request.query_params.get("camera")
        return qs.filter(camera_id=cam) if cam else qs
