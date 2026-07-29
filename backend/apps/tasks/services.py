"""Правила работы с задачами. Вся логика — здесь, вьюхи только маршрутизируют."""
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.eventlog.services import log_event

from .models import Task, TaskAttachment, TaskNotification

# Вложения принимаем только те, что реально появляются на телефоне цеха.
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
AUDIO_TYPES = {
    "audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4",
    "audio/aac", "audio/wav", "audio/x-m4a", "audio/m4a",
}
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENTS = 10


def _kind_for(content_type: str) -> str:
    if content_type in IMAGE_TYPES:
        return TaskAttachment.PHOTO
    if content_type in AUDIO_TYPES:
        return TaskAttachment.VOICE
    raise ValidationError({
        "detail": "Можно приложить фото или голосовое сообщение",
        "code": "unsupported_attachment",
    })


def notify_assignee(task: Task, text: str) -> TaskNotification | None:
    """Уведомление исполнителю. Самому себе задачи не «прилетают»."""
    if task.created_by_id == task.assignee_id:
        return None
    return TaskNotification.objects.create(
        user=task.assignee, task=task, text=text[:500],
    )


@transaction.atomic
def create_task(*, title: str, body: str, assignee, user, due_date=None,
                attachments=()) -> Task:
    title = " ".join(str(title or "").split())
    if not title:
        raise ValidationError({"detail": "Укажите, что нужно сделать",
                               "code": "empty_title"})
    if assignee is None:
        raise ValidationError({"detail": "Выберите исполнителя",
                               "code": "assignee_required"})
    task = Task.objects.create(
        title=title[:200], body=str(body or "").strip(),
        assignee=assignee, created_by=user, due_date=due_date,
    )
    for upload in attachments:
        add_attachment(task, upload, user)
    notify_assignee(task, f"Новая задача: {task.title}")
    log_event(
        "task", f"Задача «{task.title}» поставлена",
        user=user,
        payload={"task_id": task.pk, "assignee_id": assignee.pk,
                 "due_date": due_date.isoformat() if due_date else None},
    )
    return task


def add_attachment(task: Task, upload, user) -> TaskAttachment:
    if task.attachments.count() >= MAX_ATTACHMENTS:
        raise ValidationError({
            "detail": f"К задаче можно приложить не больше {MAX_ATTACHMENTS} файлов",
            "code": "too_many_attachments",
        })
    size = getattr(upload, "size", 0) or 0
    if size > MAX_ATTACHMENT_BYTES:
        raise ValidationError({
            "detail": "Файл больше 25 МБ", "code": "attachment_too_large",
        })
    kind = _kind_for(getattr(upload, "content_type", "") or "")
    return TaskAttachment.objects.create(
        task=task, kind=kind, file=upload,
        original_name=str(getattr(upload, "name", ""))[:255],
        size_bytes=size, uploaded_by=user,
    )


@transaction.atomic
def complete_task(task: Task, user) -> Task:
    """Закрыть задачу. Повторное закрытие ничего не меняет — операция идемпотентна."""
    task = Task.objects.select_for_update().get(pk=task.pk)
    if task.status == Task.DONE:
        return task
    task.status = Task.DONE
    task.done_at = timezone.now()
    task.done_by = user
    task.save(update_fields=["status", "done_at", "done_by", "updated_at"])
    if task.created_by_id and task.created_by_id != user.pk:
        TaskNotification.objects.create(
            user=task.created_by, task=task,
            text=f"Задача выполнена: {task.title}"[:500],
        )
    log_event(
        "task", f"Задача «{task.title}» выполнена",
        user=user, payload={"task_id": task.pk},
    )
    return task


@transaction.atomic
def reopen_task(task: Task, user) -> Task:
    """Вернуть задачу в работу, если её закрыли по ошибке."""
    task = Task.objects.select_for_update().get(pk=task.pk)
    if task.status == Task.PENDING:
        return task
    task.status = Task.PENDING
    task.done_at = None
    task.done_by = None
    task.save(update_fields=["status", "done_at", "done_by", "updated_at"])
    notify_assignee(task, f"Задача снова в работе: {task.title}")
    log_event(
        "task", f"Задача «{task.title}» возвращена в работу",
        user=user, payload={"task_id": task.pk},
    )
    return task


@transaction.atomic
def reassign_task(task: Task, assignee, user) -> Task:
    if assignee is None:
        raise ValidationError({"detail": "Выберите исполнителя",
                               "code": "assignee_required"})
    if task.assignee_id == assignee.pk:
        return task
    task.assignee = assignee
    task.save(update_fields=["assignee", "updated_at"])
    notify_assignee(task, f"Вам передана задача: {task.title}")
    log_event(
        "task", f"Задача «{task.title}» передана другому исполнителю",
        user=user, payload={"task_id": task.pk, "assignee_id": assignee.pk},
    )
    return task
