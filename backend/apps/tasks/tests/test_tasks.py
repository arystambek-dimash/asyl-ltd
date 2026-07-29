"""Задачи: постановка, закрытие, вложения и границы доступа."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.employees.models import Employee
from apps.rbac.models import Permission, Role
from apps.tasks.models import Task, TaskNotification
from apps.tasks.services import complete_task, create_task

pytestmark = pytest.mark.django_db


def _perm(code: str) -> Permission:
    section, action = code.split(".")
    perm, _ = Permission.objects.get_or_create(
        code=code, defaults={"section": section, "action": action, "label": code})
    return perm


def _staff(make_user, username, codes=(), first="И", last="И"):
    user = make_user(username=username)
    role = Role.objects.create(name=f"role-{username}")
    role.permissions.set([_perm(c) for c in codes])
    Employee.objects.create(
        user=user, first_name=first, last_name=last, phone="x", role=role)
    return user


def _photo(name="foto.jpg"):
    return SimpleUploadedFile(name, b"\xff\xd8\xff binary", content_type="image/jpeg")


def _voice(name="golos.ogg"):
    return SimpleUploadedFile(name, b"OggS binary", content_type="audio/ogg")


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


def test_oversized_attachment_is_rejected(make_user):
    boss = _staff(make_user, "boss13", ["tasks.create"])
    worker = _staff(make_user, "worker13")
    from apps.tasks.services import MAX_ATTACHMENT_BYTES, add_attachment
    from rest_framework.exceptions import ValidationError

    task = create_task(title="Задача", body="", assignee=worker, user=boss)
    big = SimpleUploadedFile("big.jpg", b"x", content_type="image/jpeg")
    big.size = MAX_ATTACHMENT_BYTES + 1

    with pytest.raises(ValidationError) as exc:
        add_attachment(task, big, boss)
    assert exc.value.detail["code"] == "attachment_too_large"


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
