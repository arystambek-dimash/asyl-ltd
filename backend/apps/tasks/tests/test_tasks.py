"""Задачи: постановка, закрытие, вложения и границы доступа."""

import time
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from apps.tasks.models import Task, TaskAttachment
from apps.tasks.services import complete_task, create_task, reassign_task, update_task

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("media_root")]


@pytest.fixture
def boss(user_with_perms):
    """Постановщик: из прав только ``tasks.create``."""
    return user_with_perms("boss", codes=["tasks.create"])


@pytest.fixture
def worker(user_with_perms):
    """Исполнитель без прав на задачи."""
    return user_with_perms("worker")


def _photo(name="foto.jpg"):
    return SimpleUploadedFile(name, b"\xff\xd8\xff binary", content_type="image/jpeg")


def _voice(name="golos.ogg"):
    return SimpleUploadedFile(name, b"OggS binary", content_type="audio/ogg")


def test_assignee_list_uses_user_names_and_sorts_them(auth_client, user_with_perms):
    viewer = user_with_perms("assignee-viewer", ["employees.view"])
    zhan = user_with_perms("assignee-zhan")
    alia = user_with_perms("assignee-alia")
    for user, first, last in ((zhan, "Жан", "Аманов"), (alia, "Алия", "Серикова")):
        user.first_name, user.last_name = first, last
        user.save(update_fields=["first_name", "last_name"])

    response = auth_client(viewer).get("/api/task-assignees/")

    assert response.status_code == 200
    names_by_id = {row["id"]: row["name"] for row in response.data}
    assert names_by_id[zhan.pk] == "Жан Аманов"
    assert names_by_id[alia.pk] == "Алия Серикова"
    ordered_ids = [row["id"] for row in response.data]
    assert ordered_ids.index(alia.pk) < ordered_ids.index(zhan.pk)


# ── Постановка ───────────────────────────────────────────────────────

def test_create_task_normalizes_title(boss, worker):
    task = create_task(title="  Убрать склад  ", body="до обеда",
                       assignee=worker, user=boss)

    assert task.status == Task.PENDING
    assert task.title == "Убрать склад"  # пробелы схлопнуты
    assert task.assignee == worker


def test_empty_title_is_rejected(boss, worker):
    from rest_framework.exceptions import ValidationError

    with pytest.raises(ValidationError) as exc:
        create_task(title="   ", body="", assignee=worker, user=boss)
    assert exc.value.detail["code"] == "empty_title"


def test_create_via_api_goes_through_serializer_and_service(auth_client, boss, worker):
    response = auth_client(boss).post(
        "/api/tasks/",
        {"title": "  Убрать   склад ", "assignee": worker.pk,
         "due_date": "2026-09-30", "attachments": [_photo(), _voice()]},
        format="multipart",
    )

    assert response.status_code == 201, response.data
    task = Task.objects.get(pk=response.data["id"])
    assert task.title == "Убрать склад"
    assert task.created_by == boss
    assert str(task.due_date) == "2026-09-30"
    assert sorted(a["kind"] for a in response.data["attachments"]) == ["photo", "voice"]
    assert response.data["can_complete"] is True


def test_create_and_edit_reject_the_same_invalid_title(auth_client, boss, worker):
    client = auth_client(boss)

    too_long = client.post(
        "/api/tasks/", {"title": "я" * 201, "assignee": worker.pk}, format="json")
    assert too_long.status_code == 400
    assert not Task.objects.exists()

    task = create_task(title="Задача", assignee=worker, user=boss)
    blank = client.patch(f"/api/tasks/{task.pk}/", {"title": "   "}, format="json")
    assert blank.status_code == 400
    squeezed = client.patch(f"/api/tasks/{task.pk}/", {"title": " Новая   формулировка "},
                            format="json")
    assert squeezed.status_code == 200
    task.refresh_from_db()
    assert task.title == "Новая формулировка"


def test_client_cannot_become_assignee(auth_client, boss, client_user):
    response = auth_client(boss).post(
        "/api/tasks/", {"title": "Задача", "assignee": client_user.pk}, format="json")

    assert response.status_code == 400
    assert "assignee" in response.data["detail"]
    assert not Task.objects.exists()


# ── Закрытие ─────────────────────────────────────────────────────────

def test_complete_is_idempotent(boss, worker):
    task = create_task(title="Задача", body="", assignee=worker, user=boss)

    first = complete_task(task, worker)
    done_at = first.done_at
    second = complete_task(first, worker)

    assert second.status == Task.DONE
    assert second.done_at == done_at  # повтор не переписывает время
    assert second.done_by == worker


def test_former_assignee_cannot_close_task_after_reassignment(user_with_perms, boss):
    former = user_with_perms("worker4b")
    replacement = user_with_perms("worker4c")
    stale = create_task(title="Задача", body="", assignee=former, user=boss)

    reassign_task(stale, replacement, boss)

    with pytest.raises(PermissionDenied):
        complete_task(stale, former)


def test_edit_of_stale_task_keeps_concurrent_completion(boss):
    stale = create_task(title="Старое", body="", assignee=boss, user=boss)
    complete_task(stale, boss)

    result = update_task(stale, {"title": "Новое"}, boss)

    assert result.title == "Новое"
    assert result.status == Task.DONE
    assert result.done_at is not None


# ── Границы доступа ──────────────────────────────────────────────────

def test_worker_sees_only_own_tasks(auth_client, user_with_perms, boss):
    mine = user_with_perms("worker6")
    other = user_with_perms("worker6b")
    create_task(title="Моя", body="", assignee=mine, user=boss)
    create_task(title="Чужая", body="", assignee=other, user=boss)

    response = auth_client(mine).get("/api/tasks/")

    titles = [row["title"] for row in response.json()]
    assert titles == ["Моя"]


def test_tasks_view_permission_opens_every_task(auth_client, user_with_perms, boss, worker):
    watcher = user_with_perms("watcher7", ["tasks.view"])
    create_task(title="Первая", body="", assignee=worker, user=boss)
    create_task(title="Вторая", body="", assignee=boss, user=boss)

    response = auth_client(watcher).get("/api/tasks/")

    assert len(response.json()) == 2


def test_creating_without_permission_is_denied(auth_client, user_with_perms, worker):
    other = user_with_perms("worker8b")

    response = auth_client(worker).post(
        "/api/tasks/", {"title": "Через API", "assignee": other.pk}, format="json")

    assert response.status_code == 403
    assert not Task.objects.exists()


def test_stranger_cannot_close_someone_elses_task(auth_client, user_with_perms, boss, worker):
    stranger = user_with_perms("stranger9", ["tasks.view"])
    task = create_task(title="Задача", body="", assignee=worker, user=boss)

    response = auth_client(stranger).post(f"/api/tasks/{task.pk}/complete/")

    assert response.status_code == 403
    task.refresh_from_db()
    assert task.status == Task.PENDING


def test_assignee_closes_via_api(auth_client, boss, worker):
    task = create_task(title="Задача", body="", assignee=worker, user=boss)

    response = auth_client(worker).post(f"/api/tasks/{task.pk}/complete/")

    assert response.status_code == 200
    assert response.json()["status"] == "done"
    assert response.json()["status_label"] == "Выполнено"


# ── Вложения ─────────────────────────────────────────────────────────

def test_photo_and_voice_attach(boss, worker):
    task = create_task(title="С вложениями", body="", assignee=worker, user=boss,
                       attachments=[_photo(), _voice()])

    kinds = sorted(task.attachments.values_list("kind", flat=True))
    assert kinds == ["photo", "voice"]


def test_unsupported_attachment_is_rejected(boss, worker):
    from rest_framework.exceptions import ValidationError

    bad = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/x-msdownload")
    with pytest.raises(ValidationError) as exc:
        create_task(title="Задача", body="", assignee=worker, user=boss,
                    attachments=[bad])
    assert exc.value.detail["code"] == "unsupported_attachment"


def test_attachment_content_is_detected_instead_of_trusting_mime(boss, worker):
    fake = SimpleUploadedFile(
        "attack.html",
        b"<script>alert(document.domain)</script>",
        content_type="image/jpeg",
    )

    from rest_framework.exceptions import ValidationError

    with pytest.raises(ValidationError) as exc:
        create_task(
            title="Поддельное вложение",
            body="",
            assignee=worker,
            user=boss,
            attachments=[fake],
        )
    assert exc.value.detail["code"] == "unsupported_attachment"


def test_attachment_download_requires_valid_short_lived_signature(
    auth_client,
    boss,
    worker,
):
    task = create_task(
        title="Скачать вложение",
        body="",
        assignee=worker,
        user=boss,
        attachments=[_photo()],
    )

    row = auth_client(worker).get("/api/tasks/").data[0]
    url = urlsplit(row["attachments"][0]["url"])
    signed_path = f"{url.path}?{url.query}"
    response = APIClient().get(signed_path)

    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert response["Cache-Control"] == "private, no-store"
    assert b"".join(response.streaming_content).startswith(b"\xff\xd8\xff")

    token = url.query.removeprefix("token=")
    replacement = "a" if token[-1] != "a" else "b"
    tampered = signed_path[: -1] + replacement
    assert APIClient().get(tampered).status_code == 404
    assert task.attachments.count() == 1


def test_visible_user_can_renew_an_expired_attachment_url(boss, worker):
    task = create_task(
        title="Обновить ссылку",
        body="",
        assignee=worker,
        user=boss,
        attachments=[_voice()],
    )
    attachment = task.attachments.get()
    now = time.time()
    client = APIClient()
    client.force_authenticate(worker)

    with patch("django.core.signing.time.time", return_value=now):
        old_url = client.get("/api/tasks/").data[0]["attachments"][0]["url"]

    with patch("django.core.signing.time.time", return_value=now + 301):
        old_url = urlsplit(old_url)
        assert APIClient().get(f"{old_url.path}?{old_url.query}").status_code == 404

        renewed = client.get(
            f"/api/tasks/{task.pk}/attachments/{attachment.pk}/url/"
        )
        assert renewed.status_code == 200
        fresh_url = urlsplit(renewed.data["url"])
        download = APIClient().get(f"{fresh_url.path}?{fresh_url.query}")

    assert download.status_code == 200
    assert b"".join(download.streaming_content).startswith(b"OggS")


def test_attachment_url_cannot_be_renewed_through_an_invisible_task(user_with_perms, boss, worker):
    stranger = user_with_perms("stranger-private-renew")
    task = create_task(
        title="Частное вложение",
        body="",
        assignee=worker,
        user=boss,
        attachments=[_photo()],
    )
    attachment = task.attachments.get()
    client = APIClient()
    client.force_authenticate(stranger)

    response = client.get(
        f"/api/tasks/{task.pk}/attachments/{attachment.pk}/url/"
    )

    assert response.status_code == 404


def test_oversized_attachment_is_rejected(boss, worker):
    from rest_framework.exceptions import ValidationError

    from apps.tasks.services import MAX_ATTACHMENT_BYTES, add_attachments

    task = create_task(title="Задача", body="", assignee=worker, user=boss)
    big = SimpleUploadedFile("big.jpg", b"x", content_type="image/jpeg")
    big.size = MAX_ATTACHMENT_BYTES + 1

    with pytest.raises(ValidationError) as exc:
        add_attachments(task, [big], boss)
    assert exc.value.detail["code"] == "attachment_too_large"


def test_oversized_attachment_batch_is_rejected_before_any_file_is_saved(
    boss,
    worker,
):
    from rest_framework.exceptions import ValidationError

    from apps.tasks.services import add_attachments

    task = create_task(title="Задача", body="", assignee=worker, user=boss)
    uploads = [_photo(f"photo-{index}.jpg") for index in range(4)]
    for upload in uploads:
        upload.size = 20 * 1024 * 1024

    with pytest.raises(ValidationError) as exc:
        add_attachments(task, uploads, boss)

    assert exc.value.detail["code"] == "attachments_too_large"
    assert not task.attachments.exists()


def test_partial_batch_failure_removes_files_and_rows(
    monkeypatch,
    settings,
    boss,
    worker,
):
    task = create_task(title="Задача", body="", assignee=worker, user=boss)
    original_save = TaskAttachment.save
    calls = 0

    def fail_second_save(instance, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("storage batch failed")
        return original_save(instance, *args, **kwargs)

    monkeypatch.setattr(TaskAttachment, "save", fail_second_save)

    from apps.tasks.services import add_attachments

    with pytest.raises(OSError, match="storage batch failed"):
        add_attachments(task, [_photo("first.jpg"), _photo("second.jpg")], boss)

    assert not task.attachments.exists()
    assert not [path for path in settings.MEDIA_ROOT.rglob("*") if path.is_file()]


def test_task_creation_rollback_removes_written_files(
    monkeypatch,
    settings,
    boss,
    worker,
):
    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("apps.tasks.services.log_event", fail_audit)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        create_task(
            title="Откат",
            body="",
            assignee=worker,
            user=boss,
            attachments=[_photo()],
        )

    assert not Task.objects.filter(title="Откат").exists()
    assert not [path for path in settings.MEDIA_ROOT.rglob("*") if path.is_file()]


@pytest.mark.django_db(transaction=True)
def test_task_delete_removes_file_only_after_commit(boss, worker):
    task = create_task(
        title="Удалить файл",
        body="",
        assignee=worker,
        user=boss,
        attachments=[_photo()],
    )
    attachment = task.attachments.get()
    storage = attachment.file.storage
    name = attachment.file.name
    task_id = task.pk

    with transaction.atomic():
        task.delete()
        assert storage.exists(name)
        transaction.set_rollback(True)

    assert Task.objects.filter(pk=task_id).exists()
    assert storage.exists(name)

    with transaction.atomic():
        Task.objects.get(pk=task_id).delete()
        assert storage.exists(name)

    assert not storage.exists(name)


@pytest.mark.django_db(transaction=True)
def test_shared_attachment_file_is_deleted_after_last_reference(
    boss,
    worker,
):
    first_task = create_task(
        title="Первая ссылка",
        body="",
        assignee=worker,
        user=boss,
        attachments=[_photo()],
    )
    first = first_task.attachments.get()
    second_task = create_task(
        title="Вторая ссылка",
        body="",
        assignee=worker,
        user=boss,
    )
    second = TaskAttachment.objects.create(
        task=second_task,
        kind=first.kind,
        file=first.file.name,
        original_name=first.original_name,
        size_bytes=first.size_bytes,
        uploaded_by=boss,
    )
    storage = first.file.storage
    name = first.file.name

    first.delete()
    assert storage.exists(name)

    second.delete()
    assert not storage.exists(name)


# ── Правка и удаление ────────────────────────────────────────────────

def test_creator_edits_task(auth_client, user_with_perms, boss, worker):
    other = user_with_perms("worker15b")
    task = create_task(title="Опечятка", body="", assignee=worker, user=boss)

    response = auth_client(boss).patch(
        f"/api/tasks/{task.id}/",
        {"title": "Опечатка исправлена", "assignee": other.id, "due_date": None},
        format="json",
    )

    assert response.status_code == 200
    task.refresh_from_db()
    assert task.title == "Опечатка исправлена"
    assert task.assignee_id == other.id


def test_worker_cannot_edit_task(auth_client, boss, worker):
    task = create_task(title="Задача", body="", assignee=worker, user=boss)

    response = auth_client(worker).patch(
        f"/api/tasks/{task.id}/", {"title": "Хак"}, format="json")

    assert response.status_code == 403


def test_creator_deletes_task_but_stranger_cannot(auth_client, user_with_perms, boss, worker):
    stranger = user_with_perms("boss17b", ["tasks.create", "tasks.view"])
    task = create_task(title="Дубль", body="", assignee=worker, user=boss)

    # Кнопку «Удалить» фронт показывает по can_delete — то же правило, что у DELETE.
    assert auth_client(stranger).get(f"/api/tasks/{task.id}/").data["can_delete"] is False
    assert auth_client(boss).get(f"/api/tasks/{task.id}/").data["can_delete"] is True

    # Чужой постановщик видит задачу, но снять её не может.
    assert auth_client(stranger).delete(f"/api/tasks/{task.id}/").status_code == 403
    assert Task.objects.filter(pk=task.id).exists()

    assert auth_client(boss).delete(f"/api/tasks/{task.id}/").status_code == 204
    assert not Task.objects.filter(pk=task.id).exists()


def test_client_cannot_become_assignee_on_edit(auth_client, boss, worker, client_user):
    task = create_task(title="Задача", body="", assignee=worker, user=boss)

    response = auth_client(boss).patch(
        f"/api/tasks/{task.id}/", {"assignee": client_user.pk}, format="json")

    assert response.status_code == 400
    task.refresh_from_db()
    assert task.assignee == worker
