from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0016_ai_session_conveyor_control"),
        ("conveyors", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="aicountingsession",
            name="conveyor_transport",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "No controller"),
                    ("direct", "Direct camera-PC controller"),
                    ("cloud", "Cloud lease controller"),
                ],
                db_default="",
                default="",
                max_length=8,
            ),
        ),
    ]
