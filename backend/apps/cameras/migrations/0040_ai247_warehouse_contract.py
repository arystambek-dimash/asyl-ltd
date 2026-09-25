"""Contract of the multi-warehouse rollout for AI 24/7 production.

Every production batch and camera route has an explicit warehouse; the
rollback window of the pre-warehouse image is closed.  The columns become
mandatory and the posting trigger keeps only the live invariants: the receipt
belongs to the batch warehouse and the product has a stock card there.
"""

from importlib import import_module

import django.db.models.deletion
from django.db import migrations, models

_route = import_module("apps.cameras.migrations.0029_ai247_warehouse_route")
_batch_guard = import_module("apps.cameras.migrations.0030_multi_warehouse_stock_guard")


def backfill_main_warehouse(apps, schema_editor):
    Warehouse = apps.get_model("warehouse", "Warehouse")
    main = Warehouse.objects.get(code="main")
    for model_name in ("AlwaysOnStockBatch", "AlwaysOnWarehouseRoute"):
        model = apps.get_model("cameras", model_name)
        model.objects.filter(warehouse__isnull=True).update(warehouse=main)
    if schema_editor.connection.vendor == "postgresql":
        # Fire the deferred FK checks before ALTER TABLE in this transaction.
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def install_posting_guard(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        DROP TRIGGER IF EXISTS cameras_compat_pin_batch_warehouse_bi
            ON public.cameras_alwaysonstockposting;
        DROP FUNCTION IF EXISTS public.cameras_compat_pin_batch_warehouse();

        CREATE OR REPLACE FUNCTION public.cameras_posting_requires_stock_card()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            batch_warehouse bigint;
        BEGIN
            SELECT warehouse_id INTO batch_warehouse
            FROM public.cameras_alwaysonstockbatch
            WHERE id = NEW.batch_id
            FOR UPDATE;

            IF EXISTS (
                SELECT 1
                FROM public.warehouse_stockreceipt
                WHERE id = NEW.receipt_id
                  AND warehouse_id <> batch_warehouse
            ) THEN
                RAISE EXCEPTION
                    'AI posting receipt and batch belong to different warehouses'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM public.warehouse_stockitem
                WHERE product_id = NEW.product_id
                  AND warehouse_id = batch_warehouse
            ) THEN
                RAISE EXCEPTION
                    'AI posting product %% has no stock card in warehouse %%',
                    NEW.product_id, batch_warehouse
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        DROP TRIGGER IF EXISTS cameras_posting_requires_stock_card_bi
            ON public.cameras_alwaysonstockposting;
        CREATE TRIGGER cameras_posting_requires_stock_card_bi
        BEFORE INSERT OR UPDATE OF batch_id, product_id, receipt_id
            ON public.cameras_alwaysonstockposting
        FOR EACH ROW EXECUTE FUNCTION public.cameras_posting_requires_stock_card();
        """
    )


def restore_rollout_posting_guard(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        DROP TRIGGER IF EXISTS cameras_posting_requires_stock_card_bi
            ON public.cameras_alwaysonstockposting;
        DROP FUNCTION IF EXISTS public.cameras_posting_requires_stock_card();
        """
    )
    _route.install_legacy_batch_guard(apps, schema_editor)
    _batch_guard.install_multi_warehouse_batch_guard(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0039_remove_write_only_tech_fields"),
        ("warehouse", "0010_multi_warehouse_stock_transfer"),
    ]

    operations = [
        migrations.RunPython(backfill_main_warehouse, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="alwaysonstockbatch",
            name="warehouse",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="always_on_stock_batches",
                to="warehouse.warehouse",
            ),
        ),
        migrations.AlterField(
            model_name="alwaysonwarehouseroute",
            name="warehouse",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="always_on_routes",
                to="warehouse.warehouse",
            ),
        ),
        migrations.RunPython(install_posting_guard, restore_rollout_posting_guard),
    ]
