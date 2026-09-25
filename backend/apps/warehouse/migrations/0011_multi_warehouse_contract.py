"""Contract of the multi-warehouse rollout for stock rows.

The rollback window of the pre-warehouse image is closed: stock cards,
receipts and movements always carry their warehouse.  The columns become
mandatory, the NULL-row constraint goes away and the rollout triggers are
replaced by the invariants that stay live:

* a stock card never moves to another warehouse;
* a receipt or movement of a product with stock cards names a warehouse that
  has its card.

The main-warehouse protection from 0009 stays as it is.
"""

from importlib import import_module

import django.db.models.deletion
from django.db import migrations, models

_expand = import_module("apps.warehouse.migrations.0009_multi_warehouse_expand")
_transfer = import_module(
    "apps.warehouse.migrations.0010_multi_warehouse_stock_transfer"
)


def backfill_main_warehouse(apps, schema_editor):
    Warehouse = apps.get_model("warehouse", "Warehouse")
    main = Warehouse.objects.get(code="main")
    for model_name in ("StockItem", "StockReceipt", "StockMovement"):
        model = apps.get_model("warehouse", model_name)
        model.objects.filter(warehouse__isnull=True).update(warehouse=main)
    if schema_editor.connection.vendor == "postgresql":
        # Fire the deferred FK checks before ALTER TABLE in this transaction.
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def install_stock_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        DROP TRIGGER IF EXISTS wh_compat_stockitem_warehouse_bi
            ON public.warehouse_stockitem;
        DROP TRIGGER IF EXISTS wh_compat_stockitem_delete_bd
            ON public.warehouse_stockitem;
        DROP TRIGGER IF EXISTS wh_compat_receipt_warehouse_bi
            ON public.warehouse_stockreceipt;
        DROP TRIGGER IF EXISTS wh_compat_movement_warehouse_bi
            ON public.warehouse_stockmovement;
        DROP FUNCTION IF EXISTS public.wh_compat_stockitem_warehouse();
        DROP FUNCTION IF EXISTS public.wh_compat_protect_stockitem_delete();
        DROP FUNCTION IF EXISTS public.wh_compat_product_event_warehouse();
        DROP FUNCTION IF EXISTS public.asyl_product_wh_id(bigint);
        DROP FUNCTION IF EXISTS public.asyl_main_wh_id();

        CREATE OR REPLACE FUNCTION public.wh_stockitem_keep_warehouse()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF NEW.warehouse_id IS DISTINCT FROM OLD.warehouse_id THEN
                RAISE EXCEPTION 'stock item warehouse cannot be changed'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        DROP TRIGGER IF EXISTS wh_stockitem_keep_warehouse_bu
            ON public.warehouse_stockitem;
        CREATE TRIGGER wh_stockitem_keep_warehouse_bu
        BEFORE UPDATE OF warehouse_id ON public.warehouse_stockitem
        FOR EACH ROW EXECUTE FUNCTION public.wh_stockitem_keep_warehouse();

        CREATE OR REPLACE FUNCTION public.wh_stock_event_requires_card()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM public.warehouse_stockitem
                WHERE product_id = NEW.product_id
            ) AND NOT EXISTS (
                SELECT 1
                FROM public.warehouse_stockitem
                WHERE product_id = NEW.product_id
                  AND warehouse_id = NEW.warehouse_id
            ) THEN
                RAISE EXCEPTION
                    'product %% has no stock card in warehouse %%',
                    NEW.product_id, NEW.warehouse_id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        DROP TRIGGER IF EXISTS wh_receipt_requires_card_biu
            ON public.warehouse_stockreceipt;
        CREATE TRIGGER wh_receipt_requires_card_biu
        BEFORE INSERT OR UPDATE OF product_id, warehouse_id
            ON public.warehouse_stockreceipt
        FOR EACH ROW EXECUTE FUNCTION public.wh_stock_event_requires_card();

        DROP TRIGGER IF EXISTS wh_movement_requires_card_biu
            ON public.warehouse_stockmovement;
        CREATE TRIGGER wh_movement_requires_card_biu
        BEFORE INSERT OR UPDATE OF product_id, warehouse_id
            ON public.warehouse_stockmovement
        FOR EACH ROW EXECUTE FUNCTION public.wh_stock_event_requires_card();
        """
    )


def restore_rollout_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        DROP TRIGGER IF EXISTS wh_stockitem_keep_warehouse_bu
            ON public.warehouse_stockitem;
        DROP TRIGGER IF EXISTS wh_receipt_requires_card_biu
            ON public.warehouse_stockreceipt;
        DROP TRIGGER IF EXISTS wh_movement_requires_card_biu
            ON public.warehouse_stockmovement;
        DROP FUNCTION IF EXISTS public.wh_stockitem_keep_warehouse();
        DROP FUNCTION IF EXISTS public.wh_stock_event_requires_card();
        """
    )
    _expand.install_legacy_write_guards(apps, schema_editor)
    _transfer.install_multi_warehouse_write_guards(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("warehouse", "0010_multi_warehouse_stock_transfer"),
        # Their triggers stop calling the rollout helpers dropped here.
        ("cameras", "0040_ai247_warehouse_contract"),
        ("orders", "0047_order_warehouse_contract"),
    ]

    operations = [
        migrations.RunPython(backfill_main_warehouse, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="stockitem",
            name="wh_stock_null_product_uniq",
        ),
        migrations.AlterField(
            model_name="stockitem",
            name="warehouse",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="stock_items",
                to="warehouse.warehouse",
            ),
        ),
        migrations.AlterField(
            model_name="stockreceipt",
            name="warehouse",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="receipts",
                to="warehouse.warehouse",
            ),
        ),
        migrations.AlterField(
            model_name="stockmovement",
            name="warehouse",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="movements",
                to="warehouse.warehouse",
            ),
        ),
        migrations.RunPython(install_stock_guards, restore_rollout_guards),
    ]
