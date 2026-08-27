from django.db import migrations

LEGACY_CONVEYOR_TABLE = "conveyors_conveyordevice"
AI_SESSION_TABLE = "cameras_aicountingsession"
LEGACY_AI_SESSION_COLUMNS = (
    "target_total",
    "conveyor_enabled",
    "conveyor_transport",
    "conveyor_observation_mode",
    "legacy_bridge_boot_id",
)


def remove_legacy_esp_schema(apps, schema_editor):
    """Remove ESP storage left by releases that applied migrations 0016-0018."""
    connection = schema_editor.connection
    quote_name = schema_editor.quote_name

    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))

    if LEGACY_CONVEYOR_TABLE in tables:
        cascade = " CASCADE" if connection.vendor == "postgresql" else ""
        schema_editor.execute(
            f"DROP TABLE {quote_name(LEGACY_CONVEYOR_TABLE)}{cascade}"
        )

    if AI_SESSION_TABLE in tables:
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(
                cursor,
                AI_SESSION_TABLE,
            )
        columns = {field.name for field in description}
        cascade = " CASCADE" if connection.vendor == "postgresql" else ""
        for column in LEGACY_AI_SESSION_COLUMNS:
            if column in columns:
                schema_editor.execute(
                    f"ALTER TABLE {quote_name(AI_SESSION_TABLE)} "
                    f"DROP COLUMN {quote_name(column)}{cascade}"
                )

    content_type = apps.get_model("contenttypes", "ContentType")
    permission = apps.get_model("auth", "Permission")
    legacy_content_types = content_type.objects.filter(app_label="conveyors")
    permission.objects.filter(content_type__in=legacy_content_types).delete()
    legacy_content_types.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0022_vehicle_plate_automation"),
    ]

    operations = [
        migrations.RunPython(
            remove_legacy_esp_schema,
            migrations.RunPython.noop,
        ),
    ]
