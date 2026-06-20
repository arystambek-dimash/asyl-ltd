import pytest
from django.contrib.auth.models import Group

pytestmark = pytest.mark.django_db


def test_seed_groups_exist():
    for name in ("manager", "accountant", "operator", "boss"):
        assert Group.objects.filter(name=name).exists()
