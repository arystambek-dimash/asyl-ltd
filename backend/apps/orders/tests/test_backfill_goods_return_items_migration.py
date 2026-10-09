"""Бэкфилл возвратов до приёмки (миграция 0052): «Полностью возвращено», мука — из строк."""
import importlib
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import GoodsReturn, GoodsReturnItem, GoodsReturnLine, Order, OrderItem
from apps.warehouse.services import get_default_warehouse

pytestmark = pytest.mark.django_db

MIGRATION = ("orders", "0052_backfill_goods_return_items")
migration = importlib.import_module("apps.orders.migrations.0052_backfill_goods_return_items")


def _historical_apps():
    return MigrationLoader(connection).project_state(MIGRATION).apps


def _line(goods_return, order, product, bags, *, label="", bonus=False):
    price = Decimal(0) if bonus else Decimal("1000")
    item = OrderItem.objects.create(
        order=order, product=product, quantity=50, unit_price=price, product_label_snapshot=label, is_bonus=bonus,
    )
    GoodsReturnLine.objects.create(
        goods_return=goods_return, order_item=item, bags=bags, unit_price=price, amount=bags * price,
    )


def test_old_returns_become_fully_returned_with_their_flour(make_user):
    author = make_user("old-return-author")
    client = Client.objects.create_with_user(first_name="Дана", phone="backfill")
    first, second = (Order.objects.create(client=client, status="shipped") for _ in range(2))
    dikhan = Product.objects.create(name="Первый сорт DIKHAN 50кг", color="Blue", weight_kg=Decimal("50"))
    korol = Product.objects.create(name="Второй сорт", color="Green", weight_kg=Decimal("25"))
    created = timezone.now() - timedelta(days=3)
    # Старый образ проводил сразу: колонка статуса заполнялась db_default «full».
    old = GoodsReturn.objects.create(
        client=client, settlement="debt", warehouse=get_default_warehouse(), created_by=author, status="full",
    )
    GoodsReturn.objects.filter(pk=old.pk).update(created_at=created)
    _line(old, first, dikhan, 3)
    _line(old, second, dikhan, 4)
    _line(old, second, dikhan, 1, bonus=True)
    _line(old, second, korol, 2)
    _line(old, first, None, 1, label="Удалённая мука · Синий 50 кг")
    pending = GoodsReturn.objects.create(client=client, settlement="debt", warehouse=get_default_warehouse())
    GoodsReturnItem.objects.create(
        goods_return=pending, product=dikhan, product_label_snapshot="x", bags=5, paid_bags=5,
    )

    migration.backfill_goods_return_items(_historical_apps(), None)
    migration.backfill_goods_return_items(_historical_apps(), None)  # повтор ничего не задваивает

    old.refresh_from_db()
    assert (old.status, old.accepted_by, old.accepted_at) == ("full", author, created)
    assert sorted(
        old.items.values_list(
            "product_id", "product_label_snapshot", "bags", "paid_bags", "accepted_bags", "checked_at",
        ),
        key=lambda row: row[2],
    ) == [
        (None, "Удалённая мука · Синий 50 кг", 1, 1, 1, created),
        (korol.pk, "Второй сорт · 25 кг", 2, 2, 2, created),
        (dikhan.pk, "Первый сорт DIKHAN 50кг", 8, 7, 8, created),  # бонусный мешок — без денег
    ]
    pending.refresh_from_db()
    assert (pending.status, pending.accepted_at) == ("pending", None)
    assert list(pending.items.values_list("bags", "accepted_bags")) == [(5, None)]
