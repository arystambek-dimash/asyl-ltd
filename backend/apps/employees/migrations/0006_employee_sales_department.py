from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("clients", "0013_client_last_name_optional"),
        ("employees", "0005_employee_denied_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="sales_department",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sales_employees",
                to="clients.department",
            ),
        ),
    ]
