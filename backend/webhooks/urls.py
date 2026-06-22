from django.urls import path
from .views import CameraWebhookView

urlpatterns = [
    path("webhook/camera/", CameraWebhookView.as_view()),
]
