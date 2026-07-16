from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0013_order_loading_camera"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="notes",
            field=models.TextField(blank=True, default=""),
        ),
    ]
