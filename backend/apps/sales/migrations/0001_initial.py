from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("clients", "0013_client_last_name_optional"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="Department",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("code", models.CharField(max_length=50, unique=True)),
                        ("name", models.CharField(max_length=100)),
                        (
                            "color",
                            models.CharField(default="#315FD5", max_length=7),
                        ),
                        ("is_active", models.BooleanField(default=True)),
                        ("is_default", models.BooleanField(default=False)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                    ],
                    options={
                        "db_table": "clients_department",
                        "ordering": ["created_at", "id"],
                    },
                ),
            ],
        ),
    ]
