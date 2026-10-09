from django.db import migrations

from apps.common.migration_ops import db_on_delete


class Migration(migrations.Migration):
    """Откат образа: старый код удаляет возврат, товар или сотрудника, не зная о приёмке."""

    dependencies = [
        ("orders", "0050_goods_return_acceptance"),
    ]

    operations = [
        db_on_delete("orders", "GoodsReturnItem", "goods_return", "CASCADE"),
        db_on_delete("orders", "GoodsReturnItem", "product", "SET NULL"),
        db_on_delete("orders", "GoodsReturn", "accepted_by", "SET NULL"),
    ]
