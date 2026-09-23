"""Клиент, товар и цена из отчёта владельца — общие для тестов проведения отчёта."""
import pytest

from apps.bots.tests.samples import CONDUCT_CODES
from apps.catalog.models import ClientPrice, Product, ProductAlias
from apps.clients.models import Client
from apps.sales.models import Department
from apps.warehouse.services import receive_stock


@pytest.fixture
def department():
    return Department.objects.create(code="export", name="Экспорт")


@pytest.fixture
def client(department):
    return Client.objects.create_with_user(
        first_name="Осиё", phone="+998 90 111 22 33", company_name="ООО OSIYO NAV NIHOL",
        currency="USD", department=department, country="Узбекистан",
    )


@pytest.fixture
def product(boss):
    item = Product.objects.create(name="Мука высший сорт", color="Red", weight_kg="50")
    receive_stock(item, 20000, boss)
    ProductAlias.objects.create(code="Д1с", product=item)
    return item


@pytest.fixture
def price(client, product):
    return ClientPrice.objects.create(client=client, product=product, currency="USD", price="7.50")


@pytest.fixture
def conductor(user_with_perms):
    return user_with_perms("rail-conductor", codes=CONDUCT_CODES)
