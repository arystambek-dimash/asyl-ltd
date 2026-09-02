import django.db.models.deletion
from django.db import migrations, models


def backfill_main_warehouse(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    Warehouse = apps.get_model("warehouse", "Warehouse")

    # The warehouse expand migration owns creation of this stable compatibility
    # row.  Fail closed if that invariant is ever broken instead of assigning
    # historical orders to an arbitrary active/default warehouse.
    main = Warehouse.objects.get(code="main")
    Order.objects.filter(warehouse__isnull=True).update(warehouse=main)


def install_legacy_order_guard(apps, schema_editor):
    """Pin orders written by the rollback image from their first stock item."""
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

        DROP TRIGGER IF EXISTS orders_compat_pin_warehouse_biu
            ON public.orders_orderitem;
        CREATE TRIGGER orders_compat_pin_warehouse_biu
        BEFORE INSERT OR UPDATE OF product_id, order_id
            ON public.orders_orderitem
        FOR EACH ROW EXECUTE FUNCTION public.orders_compat_pin_warehouse();
        """
    )


def uninstall_legacy_order_guard(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        DROP TRIGGER IF EXISTS orders_compat_pin_warehouse_biu
            ON public.orders_orderitem;
        DROP FUNCTION IF EXISTS public.orders_compat_pin_warehouse();
        """
    )


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0032_remove_order_scale_weighing_required"),
        ("warehouse", "0009_multi_warehouse_expand"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="warehouse",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="orders",
                to="warehouse.warehouse",
            ),
        ),
        migrations.RunPython(backfill_main_warehouse, migrations.RunPython.noop),
        migrations.RunPython(
            install_legacy_order_guard,
            uninstall_legacy_order_guard,
        ),
    ]
