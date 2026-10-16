from django.db import migrations, models
from django.db.models import F


def mark_shop_sales(apps, schema_editor):
    """Заказы «Моего магазина», оформленные до флага: сотрудник сам заказал на свой магазин."""
    Order = apps.get_model("orders", "Order")
    Order.objects.filter(
        client__employee_shop__isnull=False,
        created_by=F("client__employee_shop__employee__user"),
    ).update(is_shop_sale=True)


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0059_order_is_trial"),
        ("employees", "0012_employee_shop"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="is_shop_sale",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.RunPython(mark_shop_sales, migrations.RunPython.noop),
    ]
