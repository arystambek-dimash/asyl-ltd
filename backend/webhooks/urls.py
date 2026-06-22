from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (CameraWebhookView, CameraViewSet, WebhookCallViewSet,
                    CountView, CountCloseView, CountSessionViewSet)
from .video_views import (UploadVideoView, VideoNextView, VideoCompleteView,
                          VideoFailView, VideoRequeueView, VideoJobViewSet)

router = DefaultRouter()
router.register("cameras", CameraViewSet)
router.register("webhook-calls", WebhookCallViewSet, basename="webhook-calls")
router.register("count-sessions", CountSessionViewSet, basename="count-sessions")
router.register("video-jobs", VideoJobViewSet, basename="video-jobs")

urlpatterns = [
    path("webhook/camera/", CameraWebhookView.as_view()),
    path("count/<int:pk>/", CountView.as_view()),
    path("count/<int:pk>/close/", CountCloseView.as_view()),
    path("orders/<int:order_id>/upload-video/", UploadVideoView.as_view()),
    # Literal video-jobs sub-paths MUST come before router.urls so the router's
    # video-jobs/<pk>/ detail route does not shadow video-jobs/next/.
    path("video-jobs/next/", VideoNextView.as_view()),
    path("video-jobs/<int:pk>/complete/", VideoCompleteView.as_view()),
    path("video-jobs/<int:pk>/fail/", VideoFailView.as_view()),
    path("video-jobs/<int:pk>/requeue/", VideoRequeueView.as_view()),
] + router.urls
