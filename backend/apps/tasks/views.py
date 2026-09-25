from django.core import signing
from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasPerm, IsStaff, PermAPIViewMixin
from apps.common.signed_media import SignedMediaView

from .attachments import (
    attachment_id_from_token,
    detected_media_type,
)
from .models import Task, TaskAttachment
from .serializers import (
    TaskAttachmentSerializer,
    TaskSerializer,
)
from .services import can_delete_task, complete_task, reopen_task, visible_tasks_q


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
        queryset = (
            Task.objects.filter(visible_tasks_q(self.request.user))
            .select_related("assignee", "created_by", "done_by")
            .prefetch_related("attachments")
        )
        status = self.request.query_params.get("status")
        if status in Task.STATUSES:
            queryset = queryset.filter(status=status)
        return queryset

    def get_permissions(self):
        if self.action in ("create", "partial_update"):
            return [HasPerm("tasks.create")]
        return super().get_permissions()

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        task = self.get_object()
        return Response(self.get_serializer(complete_task(task, request.user)).data)

    @action(detail=True, methods=["post"], url_path="reopen")
    def reopen(self, request, pk=None):
        task = self.get_object()
        return Response(self.get_serializer(reopen_task(task, request.user)).data)

    @action(
        detail=True,
        methods=["get"],
        url_path=r"attachments/(?P<attachment_id>\d+)/url",
    )
    def attachment_url(self, request, pk=None, attachment_id=None):
        """Renew the short-lived download capability for a visible task."""

        task = self.get_object()
        attachment = get_object_or_404(
            task.attachments.all(),
            pk=attachment_id,
        )
        serializer = TaskAttachmentSerializer(
            attachment,
            context=self.get_serializer_context(),
        )
        return Response({"url": serializer.data["url"]})

    def perform_destroy(self, instance):
        if not can_delete_task(instance, self.request.user):
            raise PermissionDenied("Удалить задачу может только её постановщик")
        instance.delete()


class TaskAssigneeListView(PermAPIViewMixin, APIView):
    """Кому можно поручить задачу — активные сотрудники."""

    required_perms = {"get": ("tasks.create", "employees.view")}

    def get(self, request):
        from apps.employees.models import Employee
        rows = (Employee.objects.filter(is_active=True, user__is_active=True, user__is_client=False)
                .select_related("user")
                .order_by("user__first_name", "user__last_name"))
        return Response([
            {
                "id": row.user_id,
                "name": row.name,
                "position": row.position,
            }
            for row in rows
        ])


class TaskAttachmentDownloadView(SignedMediaView):
    """Serve a private task attachment through a short-lived signed URL."""

    def get(self, request, pk):
        token = request.query_params.get("token", "")
        try:
            signed_id = attachment_id_from_token(token)
        except signing.BadSignature as exc:
            raise NotFound("Вложение недоступно или ссылка устарела") from exc
        if signed_id != pk:
            raise NotFound("Вложение не найдено")

        attachment = get_object_or_404(TaskAttachment, pk=pk)
        try:
            file_handle = attachment.file.open("rb")
        except OSError as exc:
            raise NotFound("Файл вложения не найден") from exc

        detected = detected_media_type(file_handle)
        content_type = detected[1] if detected is not None else "application/octet-stream"
        return self.file_response(
            file_handle,
            content_type=content_type,
            as_attachment=detected is None,
            filename=attachment.original_name or None,
        )
