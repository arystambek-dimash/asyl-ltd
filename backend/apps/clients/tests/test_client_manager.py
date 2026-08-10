import pytest

from apps.clients.models import Client

pytestmark = pytest.mark.django_db


def test_create_with_existing_client_user_synchronizes_names(make_user):
    user = make_user(username="existing-client", client=True)

    client = Client.objects.create_with_user(
        user=user,
        first_name="Новое",
        last_name="Имя",
        phone="+7",
    )

    user.refresh_from_db()
    assert client.user_id == user.pk
    assert user.first_name == "Новое"
    assert user.last_name == "Имя"
    assert user.is_active is True
    assert user.has_usable_password() is True


def test_create_with_existing_client_user_uses_its_names_by_default(make_user):
    user = make_user(username="existing-client", client=True)
    user.first_name = "Готовое"
    user.last_name = "Имя"
    user.save(update_fields=["first_name", "last_name"])

    client = Client.objects.create_with_user(user=user, phone="+7")

    assert client.user_id == user.pk
    assert client.name == "Готовое Имя"


def test_create_with_existing_staff_user_is_rejected_without_partial_write(
    make_user,
):
    user = make_user(username="staff-identity", client=False)

    with pytest.raises(ValueError, match="is_client=True"):
        Client.objects.create_with_user(
            user=user,
            first_name="Нельзя",
            last_name="Связать",
            phone="+7",
        )

    assert not Client.objects.filter(user=user).exists()
    user.refresh_from_db()
    assert user.first_name == "A"
    assert user.last_name == "B"


def test_create_with_dual_client_staff_identity_is_rejected(make_user):
    user = make_user(username="dual-role", client=True)
    user.is_staff = True
    user.save(update_fields=["is_staff"])

    with pytest.raises(ValueError, match="must not be a staff account"):
        Client.objects.create_with_user(user=user, phone="+7")

    assert not Client.objects.filter(user=user).exists()


def test_create_with_employee_identity_is_rejected(make_user):
    from apps.employees.models import Employee

    user = make_user(username="employee-client", client=True)
    Employee.objects.create(user=user, phone="+7")

    with pytest.raises(ValueError, match="must not have an employee profile"):
        Client.objects.create_with_user(user=user, phone="+7")

    assert not Client.objects.filter(user=user).exists()
