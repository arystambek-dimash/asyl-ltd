from django.db import migrations, models


def mark_existing_cloud_sessions_as_edge(apps, _schema_editor):
    session = apps.get_model("cameras", "AiCountingSession")
    session.objects.filter(
        conveyor_transport="cloud",
        conveyor_observation_mode="",
    ).update(conveyor_observation_mode="edge")


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0017_ai_session_conveyor_transport"),
    ]

    operations = [
        migrations.AddField(
            model_name="aicountingsession",
            name="conveyor_observation_mode",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "No cloud observation source"),
                    ("edge", "Camera-PC callback"),
                    ("legacy_bridge", "Backend legacy bridge"),
                ],
                db_default="",
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="aicountingsession",
            name="legacy_bridge_boot_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.RunPython(
            mark_existing_cloud_sessions_as_edge,
            migrations.RunPython.noop,
        ),
    ]
