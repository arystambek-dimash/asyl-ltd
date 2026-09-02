import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_batch_warehouse(apps, schema_editor):
    Warehouse = apps.get_model("warehouse", "Warehouse")
    AlwaysOnStockBatch = apps.get_model("cameras", "AlwaysOnStockBatch")

    warehouse = Warehouse.objects.filter(code="main").order_by("id").first()
    if warehouse is None:
        warehouse = Warehouse.objects.create(
            code="main",
            name="Основной склад",
            address="",
            is_active=True,
            is_default=True,
        )
    AlwaysOnStockBatch.objects.filter(warehouse_id__isnull=True).update(
        warehouse_id=warehouse.pk
    )


def clear_batch_warehouse(apps, schema_editor):
    AlwaysOnStockBatch = apps.get_model("cameras", "AlwaysOnStockBatch")
    AlwaysOnStockBatch.objects.update(warehouse_id=None)


def install_legacy_batch_guard(apps, schema_editor):
    """Pin rollback-image AI batches and reject cross-warehouse postings."""
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

        DROP TRIGGER IF EXISTS cameras_compat_pin_batch_warehouse_bi
            ON public.cameras_alwaysonstockposting;
        CREATE TRIGGER cameras_compat_pin_batch_warehouse_bi
        BEFORE INSERT OR UPDATE OF batch_id, product_id, receipt_id
            ON public.cameras_alwaysonstockposting
        FOR EACH ROW EXECUTE FUNCTION public.cameras_compat_pin_batch_warehouse();
        """
    )


def uninstall_legacy_batch_guard(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        DROP TRIGGER IF EXISTS cameras_compat_pin_batch_warehouse_bi
            ON public.cameras_alwaysonstockposting;
        DROP FUNCTION IF EXISTS public.cameras_compat_pin_batch_warehouse();
        """
    )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("warehouse", "0009_multi_warehouse_expand"),
        ("cameras", "0028_separate_shipping_ai247_analytics"),
    ]

    operations = [
        migrations.CreateModel(
            name="AlwaysOnWarehouseRoute",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("camera", models.CharField(max_length=32, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="always_on_warehouse_route_updates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "warehouse",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="always_on_routes",
                        to="warehouse.warehouse",
                    ),
                ),
            ],
            options={"ordering": ["camera"]},
        ),
        migrations.AddField(
            model_name="alwaysonstockbatch",
            name="warehouse",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="always_on_stock_batches",
                to="warehouse.warehouse",
            ),
        ),
        migrations.RunPython(
            backfill_batch_warehouse,
            clear_batch_warehouse,
        ),
        migrations.RunPython(
            install_legacy_batch_guard,
            uninstall_legacy_batch_guard,
        ),
    ]
