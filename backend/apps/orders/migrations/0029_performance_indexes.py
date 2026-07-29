"""Индексы под чтение заказов: у таблицы заказов их не было ни одного.

LiveOrderManager добавляет ``deleted_at IS NULL`` к каждому запросу в системе,
поэтому индексы частичные — корзина в них не попадает, а планировщик может
брать их для любого списка заказов. Отдельный частичный индекс покрывает
обратный случай (раздел «Удалённые»).

Индексы строятся CONCURRENTLY: заказы и оплаты — горячие таблицы, а обычный
CREATE INDEX держит на них блокировку записи всё время построения, что на проде
означало бы остановку приёма заказов и кассы. Миграция поэтому неатомарна
(CONCURRENTLY нельзя выполнять внутри транзакции).
"""

from django.conf import settings
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("clients", "0013_client_last_name_optional"),
        ("orders", "0028_payment_payment_paid_at_desc_idx_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=["-created_at"],
                name="order_live_created_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=["status", "-created_at"],
                name="order_live_status_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=["department", "-created_at"],
                name="order_live_dept_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(
                condition=models.Q(("deleted_at__isnull", False)),
                fields=["-deleted_at"],
                name="order_trash_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="payment",
            index=models.Index(
                fields=["status", "method"], name="payment_status_method_idx"
            ),
        ),
    ]
