"""Задачи: постановка, закрытие, вложения и границы доступа."""

import time
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from rest_framework.test import APIClient

from apps.employees.models import Employee
from apps.sys_permissions.models import Permission
from apps.tasks.models import Task, TaskAttachment, TaskNotification
from apps.tasks.services import complete_task, create_task

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _isolated_task_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


def _perm(code: str) -> Permission:
    section, action = code.split(".")
    perm, _ = Permission.objects.get_or_create(
        code=code, defaults={"section": section, "action": action, "label": code})
    return perm


def _staff(make_user, username, codes=(), first="И", last="И"):
    user = make_user(username=username)
    user.first_name = first
    user.last_name = last
    user.save(update_fields=["first_name", "last_name"])
    employee = Employee.objects.create(user=user, phone="x")
    employee.permissions.set([_perm(code) for code in codes])
    return user


def _photo(name="foto.jpg"):
    return SimpleUploadedFile(name, b"\xff\xd8\xff binary", content_type="image/jpeg")


def _voice(name="golos.ogg"):
    return SimpleUploadedFile(name, b"OggS binary", content_type="audio/ogg")


def test_assignee_list_uses_user_names_and_sorts_them(auth_client, make_user):
    viewer = _staff(make_user, "assignee-viewer", ["employees.view"])
    zhan = _staff(make_user, "assignee-zhan", first="Жан", last="Аманов")
    alia = _staff(make_user, "assignee-alia", first="Алия", last="Серикова")

    response = auth_client(viewer).get("/api/task-assignees/")

    assert response.status_code == 200
    names_by_id = {row["id"]: row["name"] for row in response.data}
    assert names_by_id[zhan.pk] == "Жан Аманов"
    assert names_by_id[alia.pk] == "Алия Серикова"
    ordered_ids = [row["id"] for row in response.data]
    assert ordered_ids.index(alia.pk) < ordered_ids.index(zhan.pk)


# ── Постановка ───────────────────────────────────────────────────────

def test_create_task_notifies_the_assignee(make_user):
    boss = _staff(make_user, "boss1", ["tasks.create"])
    worker = _staff(make_user, "worker1")

    task = create_task(title="  Убрать склад  ", body="до обеда",
                       assignee=worker, user=boss)

    assert task.status == Task.PENDING
    assert task.title == "Убрать склад"  # пробелы схлопнуты
    note = TaskNotification.objects.get(user=worker)
    assert "Убрать склад" in note.text
    assert note.is_read is False


def test_task_assigned_to_self_does_not_notify(make_user):
    boss = _staff(make_user, "boss2", ["tasks.create"])

    create_task(title="Своя задача", body="", assignee=boss, user=boss)

    assert not TaskNotification.objects.filter(user=boss).exists()


def test_empty_title_is_rejected(make_user):
    boss = _staff(make_user, "boss3", ["tasks.create"])
    worker = _staff(make_user, "worker3")
    from rest_framework.exceptions import ValidationError

    with pytest.raises(ValidationError) as exc:
        create_task(title="   ", body="", assignee=worker, user=boss)
    assert exc.value.detail["code"] == "empty_title"


# ── Закрытие ─────────────────────────────────────────────────────────

def test_complete_is_idempotent(make_user):
    boss = _staff(make_user, "boss4", ["tasks.create"])
    worker = _staff(make_user, "worker4")
    task = create_task(title="Задача", body="", assignee=worker, user=boss)

    first = complete_task(task, worker)
    done_at = first.done_at
    second = complete_task(first, worker)

    assert second.status == Task.DONE
    assert second.done_at == done_at  # повтор не переписывает время
    assert second.done_by == worker


def test_completion_notifies_the_author(make_user):
    boss = _staff(make_user, "boss5", ["tasks.create"])
    worker = _staff(make_user, "worker5")
    task = create_task(title="Помыть бункер", body="", assignee=worker, user=boss)

    complete_task(task, worker)

    assert TaskNotification.objects.filter(
        user=boss, text__icontains="выполнена").exists()


# ── Границы доступа ──────────────────────────────────────────────────

def test_worker_sees_only_own_tasks(make_user, auth_client):
    boss = _staff(make_user, "boss6", ["tasks.create"])
    mine = _staff(make_user, "worker6")
    other = _staff(make_user, "worker6b")
    create_task(title="Моя", body="", assignee=mine, user=boss)
    create_task(title="Чужая", body="", assignee=other, user=boss)

    response = auth_client(mine).get("/api/tasks/")

    titles = [row["title"] for row in response.json()]
    assert titles == ["Моя"]


def test_tasks_view_permission_opens_every_task(make_user, auth_client):
    boss = _staff(make_user, "boss7", ["tasks.create"])
    worker = _staff(make_user, "worker7")
    watcher = _staff(make_user, "watcher7", ["tasks.view"])
    create_task(title="Первая", body="", assignee=worker, user=boss)
    create_task(title="Вторая", body="", assignee=boss, user=boss)

    response = auth_client(watcher).get("/api/tasks/")

    assert len(response.json()) == 2


def test_creating_without_permission_is_denied(make_user, auth_client):
    worker = _staff(make_user, "worker8")
    other = _staff(make_user, "worker8b")

    response = auth_client(worker).post(
        "/api/tasks/", {"title": "Через API", "assignee": other.pk}, format="json")

    assert response.status_code == 403
    assert not Task.objects.exists()


def test_stranger_cannot_close_someone_elses_task(make_user, auth_client):
    boss = _staff(make_user, "boss9", ["tasks.create"])
    worker = _staff(make_user, "worker9")
    stranger = _staff(make_user, "stranger9", ["tasks.view"])
    task = create_task(title="Задача", body="", assignee=worker, user=boss)

    response = auth_client(stranger).post(f"/api/tasks/{task.pk}/complete/")

    assert response.status_code == 403
    task.refresh_from_db()
    assert task.status == Task.PENDING


def test_assignee_closes_via_api(make_user, auth_client):
    boss = _staff(make_user, "boss10", ["tasks.create"])
    worker = _staff(make_user, "worker10")
    task = create_task(title="Задача", body="", assignee=worker, user=boss)

    response = auth_client(worker).post(f"/api/tasks/{task.pk}/complete/")

    assert response.status_code == 200
    assert response.json()["status"] == "done"
    assert response.json()["status_label"] == "Выполнено"


# ── Вложения ─────────────────────────────────────────────────────────

def test_photo_and_voice_attach(make_user):
    boss = _staff(make_user, "boss11", ["tasks.create"])
    worker = _staff(make_user, "worker11")

    task = create_task(title="С вложениями", body="", assignee=worker, user=boss,
                       attachments=[_photo(), _voice()])

    kinds = sorted(task.attachments.values_list("kind", flat=True))
    assert kinds == ["photo", "voice"]


def test_unsupported_attachment_is_rejected(make_user):
    boss = _staff(make_user, "boss12", ["tasks.create"])
    worker = _staff(make_user, "worker12")
    from rest_framework.exceptions import ValidationError

    bad = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/x-msdownload")
    with pytest.raises(ValidationError) as exc:
        create_task(title="Задача", body="", assignee=worker, user=boss,
                    attachments=[bad])
    assert exc.value.detail["code"] == "unsupported_attachment"


def test_attachment_content_is_detected_instead_of_trusting_mime(make_user):
    boss = _staff(make_user, "boss-spoof", ["tasks.create"])
    worker = _staff(make_user, "worker-spoof")
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
    make_user,
    auth_client,
):
    boss = _staff(make_user, "boss-download", ["tasks.create"])
    worker = _staff(make_user, "worker-download")
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


def test_visible_user_can_renew_an_expired_attachment_url(make_user):
    boss = _staff(make_user, "boss-renew", ["tasks.create"])
    worker = _staff(make_user, "worker-renew")
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


def test_attachment_url_cannot_be_renewed_through_an_invisible_task(make_user):
    boss = _staff(make_user, "boss-private-renew", ["tasks.create"])
    worker = _staff(make_user, "worker-private-renew")
    stranger = _staff(make_user, "stranger-private-renew")
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


def test_oversized_attachment_is_rejected(make_user):
    boss = _staff(make_user, "boss13", ["tasks.create"])
    worker = _staff(make_user, "worker13")
    from rest_framework.exceptions import ValidationError

    from apps.tasks.services import MAX_ATTACHMENT_BYTES, add_attachment

    task = create_task(title="Задача", body="", assignee=worker, user=boss)
    big = SimpleUploadedFile("big.jpg", b"x", content_type="image/jpeg")
    big.size = MAX_ATTACHMENT_BYTES + 1

    with pytest.raises(ValidationError) as exc:
        add_attachment(task, big, boss)
    assert exc.value.detail["code"] == "attachment_too_large"


def test_oversized_attachment_batch_is_rejected_before_any_file_is_saved(
    make_user,
):
    boss = _staff(make_user, "boss-batch-limit", ["tasks.create"])
    worker = _staff(make_user, "worker-batch-limit")
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
    make_user,
    monkeypatch,
    settings,
):
    boss = _staff(make_user, "boss-batch-rollback", ["tasks.create"])
    worker = _staff(make_user, "worker-batch-rollback")
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
    make_user,
    monkeypatch,
    settings,
):
    boss = _staff(make_user, "boss-create-rollback", ["tasks.create"])
    worker = _staff(make_user, "worker-create-rollback")

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
def test_task_delete_removes_file_only_after_commit(make_user):
    boss = _staff(make_user, "boss-delete-file", ["tasks.create"])
    worker = _staff(make_user, "worker-delete-file")
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
    make_user,
):
    boss = _staff(make_user, "boss-shared-file", ["tasks.create"])
    worker = _staff(make_user, "worker-shared-file")
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


# ── Уведомления ──────────────────────────────────────────────────────

def test_notification_feed_and_read_all(make_user, auth_client):
    boss = _staff(make_user, "boss14", ["tasks.create"])
    worker = _staff(make_user, "worker14")
    create_task(title="Раз", body="", assignee=worker, user=boss)
    create_task(title="Два", body="", assignee=worker, user=boss)
    client = auth_client(worker)

    feed = client.get("/api/task-notifications/").json()
    assert feed["unread"] == 2
    assert len(feed["results"]) == 2

    assert client.post("/api/task-notifications/").json()["unread"] == 0
    assert client.get("/api/task-notifications/").json()["unread"] == 0


def test_notifications_are_private(make_user, auth_client):
    boss = _staff(make_user, "boss15", ["tasks.create"])
    worker = _staff(make_user, "worker15")
    nosy = _staff(make_user, "nosy15", ["tasks.view"])
    create_task(title="Задача", body="", assignee=worker, user=boss)

    assert auth_client(nosy).get("/api/task-notifications/").json()["unread"] == 0


# ── Правка и удаление ────────────────────────────────────────────────

def test_creator_edits_task(make_user, auth_client):
    boss = _staff(make_user, "boss15", ["tasks.create"])
    worker = _staff(make_user, "worker15")
    other = _staff(make_user, "worker15b")
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


def test_worker_cannot_edit_task(make_user, auth_client):
    boss = _staff(make_user, "boss16", ["tasks.create"])
    worker = _staff(make_user, "worker16")
    task = create_task(title="Задача", body="", assignee=worker, user=boss)

    response = auth_client(worker).patch(
        f"/api/tasks/{task.id}/", {"title": "Хак"}, format="json")

    assert response.status_code == 403


def test_creator_deletes_task_but_stranger_cannot(make_user, auth_client):
    boss = _staff(make_user, "boss17", ["tasks.create"])
    stranger = _staff(make_user, "boss17b", ["tasks.create", "tasks.view"])
    worker = _staff(make_user, "worker17")
    task = create_task(title="Дубль", body="", assignee=worker, user=boss)

    # Чужой постановщик видит задачу, но снять её не может.
    assert auth_client(stranger).delete(f"/api/tasks/{task.id}/").status_code == 403
    assert Task.objects.filter(pk=task.id).exists()

    assert auth_client(boss).delete(f"/api/tasks/{task.id}/").status_code == 204
    assert not Task.objects.filter(pk=task.id).exists()
