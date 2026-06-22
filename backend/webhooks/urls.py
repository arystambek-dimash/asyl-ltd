from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import CameraWebhookView, CameraViewSet, WebhookCallViewSet

router = DefaultRouter()
router.register("cameras", CameraViewSet)
router.register("webhook-calls", WebhookCallViewSet, basename="webhook-calls")

urlpatterns = [
    path("webhook/camera/", CameraWebhookView.as_view()),
] + router.urls
