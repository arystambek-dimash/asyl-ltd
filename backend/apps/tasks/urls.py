from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    TaskAssigneeListView,
    TaskAttachmentDownloadView,
    TaskViewSet,
)

router = SimpleRouter()
router.register("tasks", TaskViewSet, basename="task")

urlpatterns = [
    path("task-assignees/", TaskAssigneeListView.as_view()),
    path(
        "task-attachments/<int:pk>/",
        TaskAttachmentDownloadView.as_view(),
        name="task-attachment-download",
    ),
    path("", include(router.urls)),
]
