"""Бэкфилл номеров машин (миграция 0043): слитная запись без потери свободного текста."""
import importlib

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from apps.clients.models import Client
from apps.orders.models import Order

pytestmark = pytest.mark.django_db

MIGRATION = ("orders", "0043_backfill_truck_numbers")
migration = importlib.import_module("apps.orders.migrations.0043_backfill_truck_numbers")


def _historical_apps():
    # Историческая модель, как её видит миграция: без LiveOrderManager и all_objects.
    return MigrationLoader(connection).project_state(MIGRATION).apps


def test_backfill_normalizes_only_recognizable_truck_plates(manager):
    client = Client.objects.create_with_user(first_name="Дана", last_name="X", phone="backfill")

    def order(number, **fields):
        return Order.objects.create(client=client, truck_number=number, **fields)

    spaced = order("403 bjn 13", truck_number_set_by=manager)
    kyrgyz = order("07 KG 695 ADT")
    cyrillic = order("А123ВС77")
    compact = order("612BEX13")
    free_text = order("самовывоз")
    unknown = order("client 777")
    wagon = order("0012 3456", transport_type="train")
    deleted = order("934 ppb 13")
    Order.objects.filter(pk=deleted.pk).update(deleted_at="2026-09-01T00:00:00Z")

    migration.backfill_truck_numbers(_historical_apps(), None)

    actual = dict(Order.all_objects.values_list("id", "truck_number"))
    assert actual == {
        spaced.id: "403BJN13",
        kyrgyz.id: "07KG695ADT",
        cyrillic.id: "A123BC77",
        compact.id: "612BEX13",
        free_text.id: "самовывоз",
        unknown.id: "client 777",
        wagon.id: "0012 3456",
        deleted.id: "934PPB13",
    }
    # Владелец номера не меняется: это та же машина в другой записи.
    assert Order.objects.get(pk=spaced.pk).truck_number_set_by == manager
