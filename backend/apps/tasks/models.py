from django.conf import settings
from django.db import models


class Task(models.Model):
    """Поручение сотруднику: текст, вложения и один исполнитель.

    Состояний ровно два — «в ожидании» и «выполнено». Промежуточных нет
    намеренно: в цехе задача либо ещё висит, либо закрыта, и лишний статус
    только заставлял бы отмечать его вручную.
    """

    PENDING = "pending"
    DONE = "done"
    STATUSES = [PENDING, DONE]

    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, default=PENDING, db_index=True)
    # Исполнитель один: у задачи с двумя ответственными нет ответственного.
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assigned_tasks",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_tasks",
    )
    due_date = models.DateField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="completed_tasks",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # «Мои невыполненные, свежие сверху» — то, что открывает исполнитель.
            models.Index(fields=["assignee", "status", "-created_at"],
                         name="task_assignee_inbox_idx"),
            models.Index(fields=["status", "-created_at"], name="task_status_idx"),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_done(self) -> bool:
        return self.status == self.DONE


def attachment_path(instance: "TaskAttachment", filename: str) -> str:
    return f"tasks/{instance.task_id}/{filename}"


class TaskAttachment(models.Model):
    """Фото или голосовое сообщение, приложенное к задаче.

    Голос не расшифровывается: запись прикладывается как есть, исполнитель
    открывает задачу и слушает.
    """

    PHOTO = "photo"
    VOICE = "voice"
    FILE = "file"
    KINDS = [PHOTO, VOICE, FILE]

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")
    kind = models.CharField(max_length=10, default=FILE)
    file = models.FileField(upload_to=attachment_path)
    original_name = models.CharField(max_length=255, blank=True, default="")
    size_bytes = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="task_attachments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class TaskNotification(models.Model):
    """Уведомление сотруднику о задаче.

    Уведомления в apps.notifications адресованы клиенту (Client), а задача
    ставится сотруднику (User), поэтому модель своя, а не переиспользованная.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="task_notifications",
    )
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="notifications")
    text = models.CharField(max_length=500)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_read", "-created_at"],
                         name="task_notify_inbox_idx"),
        ]
