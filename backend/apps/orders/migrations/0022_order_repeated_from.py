from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("orders", "0021_order_currency")]

    operations = [
        migrations.AddField(
            model_name="order",
            name="repeated_from",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="repeated_orders",
                to="orders.order",
            ),
        ),
    ]
