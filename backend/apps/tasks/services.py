"""Правила работы с задачами. Вся логика — здесь, вьюхи только маршрутизируют."""
import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.eventlog.services import log_event

from .attachments import detected_media_type
from .models import Task, TaskAttachment, TaskNotification

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENTS = 10
MAX_ATTACHMENTS_TOTAL_BYTES = 75 * 1024 * 1024

logger = logging.getLogger(__name__)


def _kind_for(upload) -> str:
    detected = detected_media_type(upload)
    if detected is not None:
        return detected[0]
    raise ValidationError({
        "detail": "Файл не является поддерживаемым фото или аудиозаписью",
        "code": "unsupported_attachment",
    })


def notify_assignee(task: Task, text: str) -> TaskNotification | None:
    """Уведомление исполнителю. Самому себе задачи не «прилетают»."""
    if task.created_by_id == task.assignee_id:
        return None
    return TaskNotification.objects.create(
        user=task.assignee, task=task, text=text[:500],
    )


def create_task(*, title: str, body: str, assignee, user, due_date=None,
                attachments=()) -> Task:
    title = " ".join(str(title or "").split())
    if not title:
        raise ValidationError({"detail": "Укажите, что нужно сделать",
                               "code": "empty_title"})
    if assignee is None:
        raise ValidationError({"detail": "Выберите исполнителя",
                               "code": "assignee_required"})
    stored_files = []
    try:
        with transaction.atomic():
            task = Task.objects.create(
                title=title[:200], body=str(body or "").strip(),
                assignee=assignee, created_by=user, due_date=due_date,
            )
            created_attachments = add_attachments(task, attachments, user)
            stored_files = _stored_file_refs(created_attachments)
            notify_assignee(task, f"Новая задача: {task.title}")
            log_event(
                "task", f"Задача «{task.title}» поставлена",
                user=user,
                payload={"task_id": task.pk, "assignee_id": assignee.pk,
                         "due_date": due_date.isoformat() if due_date else None},
            )
        return task
    except BaseException:
        # PostgreSQL rolls its rows back, but FileSystemStorage is not part of
        # that transaction. Remove files written before the failure as well.
        _delete_unreferenced_files(stored_files)
        raise


def add_attachments(task: Task, uploads, user) -> list[TaskAttachment]:
    uploads = list(uploads)
    if not uploads:
        return []

    stored_files = []
    try:
        with transaction.atomic():
            locked_task = Task.objects.select_for_update().get(pk=task.pk)
            existing_sizes = list(
                locked_task.attachments.values_list("size_bytes", flat=True)
            )
            if len(existing_sizes) + len(uploads) > MAX_ATTACHMENTS:
                raise ValidationError({
                    "detail": (
                        f"К задаче можно приложить не больше "
                        f"{MAX_ATTACHMENTS} файлов"
                    ),
                    "code": "too_many_attachments",
                })

            validated = []
            for upload in uploads:
                size = getattr(upload, "size", 0) or 0
                if size > MAX_ATTACHMENT_BYTES:
                    raise ValidationError({
                        "detail": "Файл больше 25 МБ",
                        "code": "attachment_too_large",
                    })
                validated.append((upload, size, _kind_for(upload)))

            total_size = sum(existing_sizes) + sum(
                size for _, size, _ in validated
            )
            if total_size > MAX_ATTACHMENTS_TOTAL_BYTES:
                raise ValidationError({
                    "detail": "Общий размер вложений задачи больше 75 МБ",
                    "code": "attachments_too_large",
                })

            created = []
            for upload, size, kind in validated:
                attachment = TaskAttachment(
                    task=locked_task,
                    kind=kind,
                    file=upload,
                    original_name=str(getattr(upload, "name", ""))[:255],
                    size_bytes=size,
                    uploaded_by=user,
                )
                try:
                    attachment.save(force_insert=True)
                finally:
                    # FieldFile marks itself committed after storage.save(). If
                    # the following DB insert fails, this is the only reliable
                    # handle to the already-written physical file.
                    if attachment.file and attachment.file._committed:
                        stored_files.extend(_stored_file_refs([attachment]))
                created.append(attachment)
        return created
    except BaseException:
        # This runs after the inner savepoint has rolled back. A same-name row
        # is checked before deletion so legacy/shared references remain safe.
        _delete_unreferenced_files(stored_files)
        raise


def _stored_file_refs(attachments):
    return [
        (attachment.file.storage, attachment.file.name)
        for attachment in attachments
        if attachment.file and attachment.file.name
    ]


def _delete_unreferenced_files(file_refs) -> None:
    seen = set()
    for storage, name in file_refs:
        identity = (id(storage), name)
        if identity in seen:
            continue
        seen.add(identity)
        if TaskAttachment.objects.filter(file=name).exists():
            continue
        try:
            storage.delete(name)
        except Exception:
            # Never hide the transaction error that triggered cleanup. The
            # failure is still logged so operations can remove the orphan.
            logger.exception("Could not remove orphaned task attachment %s", name)


def add_attachment(task: Task, upload, user) -> TaskAttachment:
    return add_attachments(task, [upload], user)[0]


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
