from urllib.parse import urlencode

from django.urls import reverse
from rest_framework import serializers

from .attachments import signed_attachment_token
from .models import Task, TaskAttachment, TaskNotification

STATUS_LABELS = {Task.PENDING: "В ожидании", Task.DONE: "Выполнено"}


def _person(user) -> str | None:
    if user is None:
        return None
    return user.get_full_name() or user.username


class TaskAttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = TaskAttachment
        fields = ["id", "kind", "url", "original_name", "size_bytes", "created_at"]

    def get_url(self, obj):
        if not obj.file:
            return None
        request = self.context.get("request")
        url = reverse("task-attachment-download", kwargs={"pk": obj.pk})
        url = f"{url}?{urlencode({'token': signed_attachment_token(obj.pk)})}"
        return request.build_absolute_uri(url) if request else url


class TaskSerializer(serializers.ModelSerializer):
    status_label = serializers.SerializerMethodField()
    assignee_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    done_by_name = serializers.SerializerMethodField()
    attachments = TaskAttachmentSerializer(many=True, read_only=True)
    can_complete = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id", "title", "body", "status", "status_label",
            "assignee", "assignee_name", "created_by", "created_by_name",
            "due_date", "done_at", "done_by_name", "attachments",
            "can_complete", "created_at", "updated_at",
        ]
        read_only_fields = ["status", "done_at", "created_by"]

    def get_status_label(self, obj):
        return STATUS_LABELS.get(obj.status, obj.status)

    def get_assignee_name(self, obj):
        return _person(obj.assignee)

    def get_created_by_name(self, obj):
        return _person(obj.created_by)

    def get_done_by_name(self, obj):
        return _person(obj.done_by)

    def get_can_complete(self, obj):
        """Закрыть задачу может исполнитель, постановщик или суперадмин."""
        user = getattr(self.context.get("request"), "user", None)
        if user is None or not user.is_authenticated:
            return False
        return bool(
            user.is_superuser
            or obj.assignee_id == user.pk
            or obj.created_by_id == user.pk
        )


class TaskNotificationSerializer(serializers.ModelSerializer):
    task_title = serializers.CharField(source="task.title", read_only=True)
    task_status = serializers.CharField(source="task.status", read_only=True)

    class Meta:
        model = TaskNotification
        fields = ["id", "task", "task_title", "task_status", "text",
                  "is_read", "created_at"]
