from django.apps import AppConfig
from django.db import connections
from django.db.models.signals import post_migrate


def ensure_compatibility_warehouse(sender, using, **kwargs):
    """Restore the immutable ``main`` anchor after migrate/flush.

    The data migration creates it in production. ``flush`` deliberately
    removes table data in transactional test suites and then emits
    ``post_migrate``; recreating the anchor there keeps database guards usable
    without changing whichever warehouse is already the business default.
    """
    Warehouse = sender.get_model("Warehouse")
    table_names = connections[using].introspection.table_names()
    if Warehouse._meta.db_table not in table_names:
        # The warehouse app may intentionally be migrated to a state before
        # the model exists while testing migration reversibility.
        return

    warehouses = Warehouse.objects.using(using)
    has_default = warehouses.filter(is_default=True).exists()
    warehouse, _created = warehouses.get_or_create(
        code="main",
        defaults={
            "name": "Основной склад",
            "address": "",
            "is_active": True,
            "is_default": not has_default,
        },
    )
    update_fields = []
    if not warehouse.is_active:
        warehouse.is_active = True
        update_fields.append("is_active")
    if not has_default and not warehouse.is_default:
        warehouse.is_default = True
        update_fields.append("is_default")
    if update_fields:
        warehouse.save(using=using, update_fields=update_fields)


class WarehouseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.warehouse"

    def ready(self):
        post_migrate.connect(
            ensure_compatibility_warehouse,
            sender=self,
            dispatch_uid="warehouse.ensure_compatibility_warehouse",
        )
