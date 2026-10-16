import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from apps.employees.models import ShopStock
from apps.employees.shop import set_employee_shop


@pytest.mark.django_db(transaction=True)
def test_allowing_negative_shop_stock_preserves_existing_balance_and_identity(
    user_with_perms, departments, make_product,
):
    owner = user_with_perms("negative-migration-owner", department=departments[0])
    shop = set_employee_shop(owner.employee, owner, enabled=True)
    product = make_product(name="Мука до изменения остатка")
    stock = ShopStock.objects.create(shop=shop, product=product, bags=7)
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    previous = [("employees", "0013_shop_inventory")]
    try:
        executor.migrate(previous)
        old_stock = executor.loader.project_state(previous).apps.get_model("employees", "ShopStock")
        assert old_stock.objects.get(pk=stock.pk).bags == 7
        with pytest.raises(IntegrityError), transaction.atomic():
            old_stock.objects.filter(pk=stock.pk).update(bags=-3)

        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        migrated = ShopStock.objects.get(pk=stock.pk)
        assert (migrated.shop_id, migrated.product_id, migrated.bags) == (shop.pk, product.pk, 7)
        ShopStock.objects.filter(pk=stock.pk).update(bags=-3)
        migrated.refresh_from_db()
        assert migrated.bags == -3
    finally:
        MigrationExecutor(connection).migrate(latest)
