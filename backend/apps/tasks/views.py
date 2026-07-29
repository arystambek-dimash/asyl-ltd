from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasPerm, IsStaff
from apps.common.query_params import parse_iso_date

from .models import Task, TaskNotification
from .serializers import TaskNotificationSerializer, TaskSerializer
from .services import (
    add_attachment, complete_task, create_task, reassign_task, reopen_task,
)


class TaskViewSet(viewsets.ModelViewSet):
    """Задачи сотрудников.

    Свои задачи (поставленные мне или мной) видны без отдельного права —
    иначе исполнитель не смог бы прочитать то, что ему поручили. Право
    tasks.view открывает чужие задачи, tasks.create — постановку.
    """

    serializer_class = TaskSerializer
    permission_classes = [IsStaff]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        queryset = (
            Task.objects.select_related("assignee__employee", "created_by__employee",
                                        "done_by__employee")
            .prefetch_related("attachments")
        )
        if not (user.is_superuser or user.has_perm_code("tasks.view")):
            queryset = queryset.filter(Q(assignee=user) | Q(created_by=user))
        return self._filtered(queryset)

    def _filtered(self, queryset):
        params = self.request.query_params
        status = params.get("status")
        if status in Task.STATUSES:
            queryset = queryset.filter(status=status)
        assignee = params.get("assignee")
        if assignee and assignee.isdigit():
            queryset = queryset.filter(assignee_id=int(assignee))
        if params.get("mine") in ("1", "true", "True"):
            queryset = queryset.filter(assignee=self.request.user)
        return queryset

    def _require_create(self):
        user = self.request.user
        if not (user.is_superuser or user.has_perm_code("tasks.create")):
            raise PermissionDenied("Недостаточно прав для постановки задач")

    def _resolve_assignee(self, raw):
        if raw in (None, ""):
            raise ValidationError({"detail": "Выберите исполнителя",
                                   "code": "assignee_required"})
        user_model = get_user_model()
        assignee = user_model.objects.filter(pk=raw, is_client=False).first()
        if assignee is None:
            raise ValidationError({"detail": "Исполнитель не найден",
                                   "code": "assignee_not_found"})
        return assignee

    def create(self, request, *args, **kwargs):
        self._require_create()
        task = create_task(
            title=request.data.get("title"),
            body=request.data.get("body") or "",
            assignee=self._resolve_assignee(request.data.get("assignee")),
            user=request.user,
            due_date=parse_iso_date(request.data.get("due_date")),
            attachments=request.FILES.getlist("attachments"),
        )
        return Response(self.get_serializer(task).data, status=201)

    def _assert_can_close(self, task):
        user = self.request.user
        if not (user.is_superuser
                or task.assignee_id == user.pk
                or task.created_by_id == user.pk):
            raise PermissionDenied("Закрыть задачу может исполнитель или постановщик")

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        task = self.get_object()
        self._assert_can_close(task)
        return Response(self.get_serializer(complete_task(task, request.user)).data)

    @action(detail=True, methods=["post"], url_path="reopen")
    def reopen(self, request, pk=None):
        task = self.get_object()
        self._assert_can_close(task)
        return Response(self.get_serializer(reopen_task(task, request.user)).data)

    @action(detail=True, methods=["post"], url_path="reassign")
    def reassign(self, request, pk=None):
        self._require_create()
        task = self.get_object()
        assignee = self._resolve_assignee(request.data.get("assignee"))
        return Response(self.get_serializer(reassign_task(task, assignee, request.user)).data)

    @action(detail=True, methods=["post"], url_path="attachments")
    def upload(self, request, pk=None):
        task = self.get_object()
        self._assert_can_close(task)
        uploads = request.FILES.getlist("attachments") or request.FILES.getlist("file")
        if not uploads:
            raise ValidationError({"detail": "Файл не приложен", "code": "no_file"})
        for upload in uploads:
            add_attachment(task, upload, request.user)
        task.refresh_from_db()
        return Response(self.get_serializer(task).data)

    def perform_update(self, serializer):
        self._require_create()
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if not (user.is_superuser or instance.created_by_id == user.pk):
            raise PermissionDenied("Удалить задачу может только её постановщик")
        instance.delete()


class TaskNotificationView(APIView):
    """Уведомления сотрудника о задачах: список и отметка о прочтении."""

    permission_classes = [IsStaff]

    def get(self, request):
        rows = (TaskNotification.objects.filter(user=request.user)
                .select_related("task")[:50])
        return Response({
            "unread": TaskNotification.objects.filter(
                user=request.user, is_read=False).count(),
            "results": TaskNotificationSerializer(rows, many=True).data,
        })

    def post(self, request):
        TaskNotification.objects.filter(user=request.user, is_read=False).update(
            is_read=True)
        return Response({"unread": 0})


class TaskAssigneeListView(APIView):
    """Кому можно поручить задачу — активные сотрудники."""

    def get_permissions(self):
        return [HasPerm("tasks.create", "employees.view")]

    def get(self, request):
        from apps.employees.models import Employee
        rows = (Employee.objects.filter(is_active=True)
                .select_related("user").order_by("first_name", "last_name"))
        return Response([
            {
                "id": row.user_id,
                "name": f"{row.first_name} {row.last_name}".strip() or row.user.username,
                "position": row.position,
            }
            for row in rows if row.user_id
        ])
