from django.db import migrations


def show_default_department_and_warehouse(apps, schema_editor):
    """Сразу после выката «Главная» показывает основной отдел и основной склад («Мельница»)."""
    DashboardSettings = apps.get_model("dashboard", "DashboardSettings")
    Department = apps.get_model("sales", "Department")
    Warehouse = apps.get_model("warehouse", "Warehouse")
    row, _ = DashboardSettings.objects.get_or_create(singleton=True)
    row.departments.set(Department.objects.filter(is_default=True))
    row.warehouses.set(Warehouse.objects.filter(is_default=True))


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(show_default_department_and_warehouse, migrations.RunPython.noop),
    ]
