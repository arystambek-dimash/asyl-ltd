from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from .models import Camera
from .services import process_webhook


class CameraWebhookView(APIView):
    authentication_classes = []      # без JWT
    permission_classes = [AllowAny]  # вместо этого — аутентификация по ключу

    def post(self, request):
        camera_id = request.data.get("camera_id")
        camera = Camera.objects.filter(camera_id=camera_id).first()
        if camera is None:
            return Response({"detail": "Камера не найдена", "code": "camera_not_found"}, status=404)
        key = request.headers.get("X-Camera-Key", "")
        if key != camera.api_key:
            return Response({"detail": "Неверный ключ камеры", "code": "bad_key"}, status=401)
        if not camera.is_active:
            return Response({"detail": "Камера отключена", "code": "camera_inactive"}, status=403)
        response = process_webhook(camera, request.data)
        return Response(response, status=200)
