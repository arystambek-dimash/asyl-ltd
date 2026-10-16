from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0060_order_is_shop_sale"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(model_name="order", name="is_shop_sale"),
            ],
        ),
    ]
