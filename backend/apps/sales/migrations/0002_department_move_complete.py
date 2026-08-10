from django.db import migrations


class Migration(migrations.Migration):
    """Keep app-targeted ``migrate sales`` on the completed cross-app state."""

    dependencies = [
        ("sales", "0001_initial"),
        ("clients", "0014_move_department_to_sales"),
    ]

    operations = []
