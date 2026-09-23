from django.db import migrations

from apps.common.migration_ops import db_on_delete


class Migration(migrations.Migration):
    """Откат образа: старый код удаляет товар и сотрудника, не зная о словаре кодов."""

    dependencies = [
        ("catalog", "0013_productalias"),
    ]

    operations = [
        db_on_delete("catalog", "ProductAlias", "product", "CASCADE"),
        db_on_delete("catalog", "ProductAlias", "created_by", "SET NULL"),
    ]
