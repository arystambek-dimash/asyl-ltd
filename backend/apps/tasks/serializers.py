from urllib.parse import urlencode

from django.urls import reverse
from rest_framework import serializers

from .attachments import signed_attachment_token
from .models import Task, TaskAttachment
from .services import can_act, create_task, update_task

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
    can_delete = serializers.SerializerMethodField()

    def create(self, validated_data):
        request = self.context["request"]
        return create_task(
            **validated_data,
            user=request.user,
            attachments=request.FILES.getlist("attachments"),
        )

    def update(self, instance, validated_data):
        return update_task(instance, validated_data, self.context["request"].user)

    def get_can_delete(self, obj):
        from .services import can_delete_task
        user = getattr(self.context.get("request"), "user", None)
        return bool(user and user.is_authenticated and can_delete_task(obj, user))

    class Meta:
        model = Task
        fields = [
            "id", "title", "body", "status", "status_label",
            "assignee", "assignee_name", "created_by", "created_by_name",
            "due_date", "done_at", "done_by_name", "attachments",
            "can_complete", "can_delete", "created_at", "updated_at",
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
        user = getattr(self.context.get("request"), "user", None)
        return bool(user and user.is_authenticated and can_act(obj, user))
