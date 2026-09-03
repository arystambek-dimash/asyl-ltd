from django.db import migrations


def install_multi_warehouse_batch_guard(apps, schema_editor):
    """Use an explicit batch/receipt warehouse for multi-warehouse products."""
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        CREATE OR REPLACE FUNCTION public.cameras_compat_pin_batch_warehouse()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            posting_warehouse bigint;
            batch_warehouse bigint;
            receipt_warehouse bigint;
        BEGIN
            SELECT warehouse_id INTO batch_warehouse
            FROM public.cameras_alwaysonstockbatch
            WHERE id = NEW.batch_id
            FOR UPDATE;

            SELECT warehouse_id INTO receipt_warehouse
            FROM public.warehouse_stockreceipt
            WHERE id = NEW.receipt_id;

            posting_warehouse := COALESCE(
                batch_warehouse,
                receipt_warehouse,
                public.asyl_product_wh_id(NEW.product_id)
            );

            IF receipt_warehouse IS NOT NULL
               AND receipt_warehouse <> posting_warehouse THEN
                RAISE EXCEPTION
                    'AI posting receipt and batch belong to different warehouses'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM public.warehouse_stockitem
                WHERE product_id = NEW.product_id
                  AND COALESCE(
                      warehouse_id,
                      public.asyl_main_wh_id()
                  ) = posting_warehouse
            ) THEN
                RAISE EXCEPTION
                    'AI posting product %% has no stock card in warehouse %%',
                    NEW.product_id, posting_warehouse
                    USING ERRCODE = '23514';
            END IF;

            IF batch_warehouse IS NULL THEN
                UPDATE public.cameras_alwaysonstockbatch
                SET warehouse_id = posting_warehouse
                WHERE id = NEW.batch_id;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )


def restore_single_warehouse_batch_guard(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        CREATE OR REPLACE FUNCTION public.cameras_compat_pin_batch_warehouse()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            posting_warehouse bigint;
            batch_warehouse bigint;
        BEGIN
            posting_warehouse := public.asyl_product_wh_id(NEW.product_id);
            IF EXISTS (
                SELECT 1
                FROM public.warehouse_stockreceipt
                WHERE id = NEW.receipt_id
                  AND warehouse_id <> posting_warehouse
            ) THEN
                RAISE EXCEPTION
                    'AI posting receipt and product belong to different warehouses'
                    USING ERRCODE = '23514';
            END IF;

            SELECT warehouse_id INTO batch_warehouse
            FROM public.cameras_alwaysonstockbatch
            WHERE id = NEW.batch_id
            FOR UPDATE;
            IF batch_warehouse IS NULL THEN
                UPDATE public.cameras_alwaysonstockbatch
                SET warehouse_id = posting_warehouse
                WHERE id = NEW.batch_id;
            ELSIF batch_warehouse <> posting_warehouse THEN
                RAISE EXCEPTION
                    'AI batch %% cannot span warehouses %% and %%',
                    NEW.batch_id, batch_warehouse, posting_warehouse
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0029_ai247_warehouse_route"),
        ("warehouse", "0010_multi_warehouse_stock_transfer"),
    ]

    operations = [
        migrations.RunPython(
            install_multi_warehouse_batch_guard,
            restore_single_warehouse_batch_guard,
        ),
    ]
