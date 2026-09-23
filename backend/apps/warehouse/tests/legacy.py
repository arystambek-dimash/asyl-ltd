"""Помощники тестов: строки склада, записанные образом до мультисклада."""
from django.db import connection

from apps.warehouse.models import StockItem


def force_legacy_null_warehouse(stock_item):
    """Emulate a row written by the pre-warehouse application image.

    Inside a test transaction the deferred FK checks of earlier inserts are
    still pending, and PostgreSQL refuses ALTER TABLE until they fire.
    """
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute("ALTER TABLE warehouse_stockitem DISABLE TRIGGER USER")
        try:
            StockItem.objects.filter(pk=stock_item.pk).update(warehouse=None)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE warehouse_stockitem ENABLE TRIGGER USER")
                cursor.execute("SET CONSTRAINTS ALL DEFERRED")
    else:
        StockItem.objects.filter(pk=stock_item.pk).update(warehouse=None)
    stock_item.refresh_from_db()
    assert stock_item.warehouse_id is None
