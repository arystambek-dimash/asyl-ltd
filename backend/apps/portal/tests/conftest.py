import pytest

from apps.clients.models import Client


@pytest.fixture
def own_client(client_user):
    """Карточка клиента, под которой ``client_user`` входит в портал."""
    return Client.objects.create_with_user(
        first_name="Мой", last_name="К", phone="x", user=client_user)
