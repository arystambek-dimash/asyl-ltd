from django.db import migrations


def _move_content_type(apps, schema_editor, source_label, target_label):
    ContentType = apps.get_model("contenttypes", "ContentType")
    database = schema_editor.connection.alias
    source = (
        ContentType.objects.using(database)
        .filter(app_label=source_label, model="department")
        .first()
    )
    if source is None:
        return

    target = (
        ContentType.objects.using(database)
        .filter(app_label=target_label, model="department")
        .first()
    )
    if target is not None and target.pk != source.pk:
        raise RuntimeError(
            "Cannot move Department content type: both "
            f"{source_label}.department and {target_label}.department exist"
        )

    source.app_label = target_label
    source.save(using=database, update_fields=["app_label"])


def move_department_content_type(apps, schema_editor):
    _move_content_type(apps, schema_editor, "clients", "sales")


def restore_department_content_type(apps, schema_editor):
    _move_content_type(apps, schema_editor, "sales", "clients")


class Migration(migrations.Migration):
    dependencies = [
        ("clients", "0013_client_last_name_optional"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("employees", "0009_move_sales_department_relation"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="Department"),
            ],
        ),
        migrations.RunPython(
            move_department_content_type,
            restore_department_content_type,
        ),
    ]
