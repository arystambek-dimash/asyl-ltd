"""Правила работы с задачами. Вся логика — здесь, вьюхи только маршрутизируют."""
import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.eventlog.services import log_event

from .attachments import delete_file_if_unreferenced, detected_media_type
from .models import Task, TaskAttachment

MB = 1024 * 1024
MAX_ATTACHMENT_BYTES = 25 * MB
MAX_ATTACHMENTS = 10
MAX_ATTACHMENTS_TOTAL_BYTES = 75 * MB

logger = logging.getLogger(__name__)


def validate_assignee(assignee):
    employee = getattr(assignee, "employee", None)
    if (assignee is None or assignee.is_client or not assignee.is_active
            or not (assignee.is_superuser or (employee and employee.is_active))):
        raise ValidationError({"assignee": "Выберите действующего сотрудника"})
    return assignee


def _locked_task(task):
    try:
        return Task.objects.select_for_update().get(pk=task.pk)
    except Task.DoesNotExist as exc:
        raise NotFound("Задача не найдена") from exc


def can_act(task, user) -> bool:
    """Закрыть задачу или вернуть её в работу может исполнитель, постановщик или суперадмин."""
    return bool(user.is_superuser or user.pk in (task.assignee_id, task.created_by_id))


def visible_tasks_q(user) -> Q:
    """Свои задачи (поставленные мне или мной) видны всем, чужие — с правом tasks.view."""
    if user.has_perm_code("tasks.view"):
        return Q()
    return Q(assignee=user) | Q(created_by=user)


def can_see(task, user) -> bool:
    """То же правило, что visible_tasks_q, для уже загруженной задачи."""
    return user.has_perm_code("tasks.view") or can_act(task, user)


def _assert_can_act(task, user):
    if not can_act(task, user):
        raise PermissionDenied("Изменить выполнение задачи может исполнитель или постановщик")


def _assert_still_visible(task, user):
    if not can_see(task, user):
        raise NotFound("Задача больше недоступна")


def _clean_title(title) -> str:
    title = " ".join(str(title or "").split())
    if not title:
        raise ValidationError({"detail": "Укажите, что нужно сделать",
                               "code": "empty_title"})
    return title


def _kind_for(upload) -> str:
    detected = detected_media_type(upload)
    if detected is not None:
        return detected[0]
    raise ValidationError({
        "detail": "Файл не является поддерживаемым фото или аудиозаписью",
        "code": "unsupported_attachment",
    })


def create_task(*, title: str, assignee, user, body: str = "", due_date=None,
                attachments=()) -> Task:
    title = _clean_title(title)
    validate_assignee(assignee)
    stored_files = []
    try:
        with transaction.atomic():
            task = Task.objects.create(
                title=title, body=str(body or "").strip(),
                assignee=assignee, created_by=user, due_date=due_date,
            )
            created_attachments = add_attachments(task, attachments, user)
            stored_files = _stored_file_refs(created_attachments)
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
    """Приложить файлы к только что созданной задаче (вызывается из create_task)."""
    uploads = list(uploads)
    if not uploads:
        return []

    stored_files = []
    try:
        with transaction.atomic():
            if len(uploads) > MAX_ATTACHMENTS:
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
                        "detail": f"Файл больше {MAX_ATTACHMENT_BYTES // MB} МБ",
                        "code": "attachment_too_large",
                    })
                validated.append((upload, size, _kind_for(upload)))

            total_size = sum(size for _, size, _ in validated)
            if total_size > MAX_ATTACHMENTS_TOTAL_BYTES:
                raise ValidationError({
                    "detail": (
                        f"Общий размер вложений задачи больше "
                        f"{MAX_ATTACHMENTS_TOTAL_BYTES // MB} МБ"
                    ),
                    "code": "attachments_too_large",
                })

            created = []
            for upload, size, kind in validated:
                attachment = TaskAttachment(
                    task=task,
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
        try:
            delete_file_if_unreferenced(storage, name)
        except Exception:
            # Never hide the transaction error that triggered cleanup. The
            # failure is still logged so operations can remove the orphan.
            logger.exception("Could not remove orphaned task attachment %s", name)


@transaction.atomic
def complete_task(task: Task, user) -> Task:
    """Закрыть задачу. Повторное закрытие ничего не меняет — операция идемпотентна."""
    task = _locked_task(task)
    _assert_can_act(task, user)
    if task.status == Task.DONE:
        return task
    task.status = Task.DONE
    task.done_at = timezone.now()
    task.done_by = user
    task.save(update_fields=["status", "done_at", "done_by", "updated_at"])
    log_event(
        "task", f"Задача «{task.title}» выполнена",
        user=user, payload={"task_id": task.pk},
    )
    return task


@transaction.atomic
def reopen_task(task: Task, user) -> Task:
    """Вернуть задачу в работу, если её закрыли по ошибке."""
    task = _locked_task(task)
    _assert_can_act(task, user)
    if task.status == Task.PENDING:
        return task
    task.status = Task.PENDING
    task.done_at = None
    task.done_by = None
    task.save(update_fields=["status", "done_at", "done_by", "updated_at"])
    log_event(
        "task", f"Задача «{task.title}» возвращена в работу",
        user=user, payload={"task_id": task.pk},
    )
    return task


@transaction.atomic
def reassign_task(task: Task, assignee, user) -> Task:
    task = _locked_task(task)
    _assert_still_visible(task, user)
    validate_assignee(assignee)
    if task.assignee_id == assignee.pk:
        return task
    task.assignee = assignee
    task.save(update_fields=["assignee", "updated_at"])
    log_event(
        "task", f"Задача «{task.title}» передана другому исполнителю",
        user=user, payload={"task_id": task.pk, "assignee_id": assignee.pk},
    )
    return task


@transaction.atomic
def update_task(task: Task, changes: dict, user) -> Task:
    """PATCH задачи: смена исполнителя идёт через reassign_task."""
    task = _locked_task(task)
    _assert_still_visible(task, user)
    assignee = changes.get("assignee")
    if "title" in changes:
        changes = {**changes, "title": _clean_title(changes["title"])}
    fields = []
    for field in ("title", "body", "due_date"):
        if field in changes:
            setattr(task, field, changes[field])
            fields.append(field)
    if fields:
        task.save(update_fields=[*fields, "updated_at"])
    if assignee is not None:
        task = reassign_task(task, assignee, user)
    return task


def can_delete_task(task: Task, user) -> bool:
    """Снять задачу может только её постановщик или суперадмин."""
    return bool(user.is_superuser or task.created_by_id == user.pk)
