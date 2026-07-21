from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("employees", "0004_inherit_role_permissions"),
        ("rbac", "0012_shipping_rollback_permission"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="denied_permissions",
            field=models.ManyToManyField(
                blank=True,
                related_name="denied_for_employees",
                to="rbac.permission",
            ),
        ),
    ]
