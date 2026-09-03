from django.db import migrations


def install_multi_warehouse_order_guard(apps, schema_editor):
    """Use an order's explicit warehouse; legacy ambiguous orders fail closed."""
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        CREATE OR REPLACE FUNCTION public.orders_compat_pin_warehouse()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            product_warehouse bigint;
            order_warehouse bigint;
        BEGIN
            IF NEW.product_id IS NULL THEN
                RETURN NEW;
            END IF;

            SELECT warehouse_id INTO order_warehouse
            FROM public.orders_order
            WHERE id = NEW.order_id
            FOR UPDATE;

            IF order_warehouse IS NULL THEN
                -- A rollback image cannot send the new warehouse field. Keep
                -- it working only while this product has one unambiguous home.
                product_warehouse := public.asyl_product_wh_id(NEW.product_id);
                UPDATE public.orders_order
                SET warehouse_id = product_warehouse
                WHERE id = NEW.order_id;
                order_warehouse := product_warehouse;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.warehouse_stockitem
                WHERE product_id = NEW.product_id
            ) AND NOT EXISTS (
                SELECT 1
                FROM public.warehouse_stockitem
                WHERE product_id = NEW.product_id
                  AND COALESCE(
                      warehouse_id,
                      public.asyl_main_wh_id()
                  ) = order_warehouse
            ) THEN
                RAISE EXCEPTION
                    'order %% product %% has no stock card in warehouse %%',
                    NEW.order_id, NEW.product_id, order_warehouse
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )


def restore_single_warehouse_order_guard(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        CREATE OR REPLACE FUNCTION public.orders_compat_pin_warehouse()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            product_warehouse bigint;
            order_warehouse bigint;
        BEGIN
            IF NEW.product_id IS NULL THEN
                RETURN NEW;
            END IF;

            product_warehouse := public.asyl_product_wh_id(NEW.product_id);

            SELECT warehouse_id INTO order_warehouse
            FROM public.orders_order
            WHERE id = NEW.order_id
            FOR UPDATE;
            IF order_warehouse IS NULL THEN
                UPDATE public.orders_order
                SET warehouse_id = product_warehouse
                WHERE id = NEW.order_id;
            ELSIF order_warehouse <> product_warehouse THEN
                RAISE EXCEPTION
                    'order %% cannot contain products from warehouses %% and %%',
                    NEW.order_id, order_warehouse, product_warehouse
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0033_order_warehouse"),
        ("warehouse", "0010_multi_warehouse_stock_transfer"),
    ]

    operations = [
        migrations.RunPython(
            install_multi_warehouse_order_guard,
            restore_single_warehouse_order_guard,
        ),
    ]
