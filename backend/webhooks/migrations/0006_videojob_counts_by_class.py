from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("webhooks", "0005_delete_countsession"),
    ]

    operations = [
        migrations.AddField(
            model_name="videojob",
            name="counts_by_class",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
