from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (CameraWebhookView, CameraViewSet, WebhookCallViewSet,
                    CountView, CountCloseView, CountSessionViewSet)

router = DefaultRouter()
router.register("cameras", CameraViewSet)
router.register("webhook-calls", WebhookCallViewSet, basename="webhook-calls")
router.register("count-sessions", CountSessionViewSet, basename="count-sessions")

urlpatterns = [
    path("webhook/camera/", CameraWebhookView.as_view()),
    path("count/<int:pk>/", CountView.as_view()),
    path("count/<int:pk>/close/", CountCloseView.as_view()),
] + router.urls
