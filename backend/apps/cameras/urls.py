from django.urls import path
from .views import CameraAuthView, CameraListView, CameraTokenView

urlpatterns = [
    path("cameras/", CameraListView.as_view()),
    path("cameras/token/", CameraTokenView.as_view()),
    path("cameras/auth/", CameraAuthView.as_view()),
]
