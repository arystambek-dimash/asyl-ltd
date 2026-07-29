from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import TaskAssigneeListView, TaskNotificationView, TaskViewSet

router = DefaultRouter()
router.register("tasks", TaskViewSet, basename="task")

urlpatterns = [
    path("task-notifications/", TaskNotificationView.as_view()),
    path("task-assignees/", TaskAssigneeListView.as_view()),
    path("", include(router.urls)),
]
