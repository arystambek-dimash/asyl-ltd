"""Contract of the multi-warehouse rollout for orders.

Every order has an explicit warehouse since 0033; the rollback window of the
pre-warehouse image is closed.  The column becomes mandatory and the order
item trigger keeps only the live invariant: a product that has stock cards
must have one in the order's warehouse.
"""

from importlib import import_module

import django.db.models.deletion
from django.db import migrations, models

_order_warehouse = import_module("apps.orders.migrations.0033_order_warehouse")
_order_guard = import_module("apps.orders.migrations.0034_multi_warehouse_stock_guard")


def backfill_main_warehouse(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    Warehouse = apps.get_model("warehouse", "Warehouse")
    main = Warehouse.objects.get(code="main")
    Order.objects.filter(warehouse__isnull=True).update(warehouse=main)
    if schema_editor.connection.vendor == "postgresql":
        # Fire the deferred FK checks before ALTER TABLE in this transaction.
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def install_order_item_guard(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        DROP TRIGGER IF EXISTS orders_compat_pin_warehouse_biu
            ON public.orders_orderitem;
        DROP FUNCTION IF EXISTS public.orders_compat_pin_warehouse();

        CREATE OR REPLACE FUNCTION public.orders_item_requires_stock_card()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            order_warehouse bigint;
        BEGIN
            IF NEW.product_id IS NULL THEN
                RETURN NEW;
            END IF;

            SELECT warehouse_id INTO order_warehouse
            FROM public.orders_order
            WHERE id = NEW.order_id
            FOR UPDATE;

            IF EXISTS (
                SELECT 1
                FROM public.warehouse_stockitem
                WHERE product_id = NEW.product_id
            ) AND NOT EXISTS (
                SELECT 1
                FROM public.warehouse_stockitem
                WHERE product_id = NEW.product_id
                  AND warehouse_id = order_warehouse
            ) THEN
                RAISE EXCEPTION
                    'order %% product %% has no stock card in warehouse %%',
                    NEW.order_id, NEW.product_id, order_warehouse
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        DROP TRIGGER IF EXISTS orders_item_requires_stock_card_biu
            ON public.orders_orderitem;
        CREATE TRIGGER orders_item_requires_stock_card_biu
        BEFORE INSERT OR UPDATE OF product_id, order_id
            ON public.orders_orderitem
        FOR EACH ROW EXECUTE FUNCTION public.orders_item_requires_stock_card();
        """
    )


def restore_rollout_order_guard(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        DROP TRIGGER IF EXISTS orders_item_requires_stock_card_biu
            ON public.orders_orderitem;
        DROP FUNCTION IF EXISTS public.orders_item_requires_stock_card();
        """
    )
    _order_warehouse.install_legacy_order_guard(apps, schema_editor)
    _order_guard.install_multi_warehouse_order_guard(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0046_remove_dead_order_fields"),
        ("warehouse", "0010_multi_warehouse_stock_transfer"),
    ]

    operations = [
        migrations.RunPython(backfill_main_warehouse, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="order",
            name="warehouse",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="orders",
                to="warehouse.warehouse",
            ),
        ),
        migrations.RunPython(install_order_item_guard, restore_rollout_order_guard),
    ]
