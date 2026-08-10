from django.db import migrations


class Migration(migrations.Migration):
    """Keep app-targeted ``migrate employees`` on the completed move state."""

    dependencies = [
        ("employees", "0009_move_sales_department_relation"),
        ("clients", "0014_move_department_to_sales"),
    ]

    operations = []
