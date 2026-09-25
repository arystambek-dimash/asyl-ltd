from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0015_productalias_spelling"),
    ]

    operations = [
        # Expand/contract rollout: the base price has been NULL since 0009 and
        # nothing reads it. Drop it from Django state now; the nullable column
        # stays for old workers and an image rollback until the next release.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="product",
                    name="price",
                ),
            ],
            database_operations=[],
        ),
    ]
