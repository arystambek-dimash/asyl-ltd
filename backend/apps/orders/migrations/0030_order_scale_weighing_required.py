from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0029_performance_indexes"),
    ]

    operations = [
        # Existing orders (including the production truck already loading at
        # rollout time) are legacy and must not be reinterpreted as having a
        # physical scale entry. New ORM instances use True after AlterField.
        migrations.AddField(
            model_name="order",
            name="scale_weighing_required",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AlterField(
            model_name="order",
            name="scale_weighing_required",
            field=models.BooleanField(db_default=True, default=True),
        ),
    ]
