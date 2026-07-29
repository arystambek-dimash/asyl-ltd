"""Индекс под сортировку истории AI-сессий.

``Meta.ordering = ["-started_at"]`` применяется к каждому чтению истории и
списка сессий, но индекса под него не было — база сортировала всю таблицу.
Строится CONCURRENTLY: сессии пишутся во время погрузки, блокировать таблицу
на время построения нельзя.
"""

from django.conf import settings
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("cameras", "0011_monoblockdevice"),
        ("orders", "0029_performance_indexes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="aicountingsession",
            index=models.Index(fields=["-started_at"], name="ai_session_started_idx"),
        ),
    ]
