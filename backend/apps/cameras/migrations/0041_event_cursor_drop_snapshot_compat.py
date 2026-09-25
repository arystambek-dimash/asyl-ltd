from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0040_ai247_warehouse_contract"),
    ]

    operations = [
        # Новый код больше не зеркалит журнал событий в last_total, поэтому
        # ограничение снимаем и в базе: образ автоотката пишет оба поля сам.
        migrations.RemoveConstraint(
            model_name="alwaysoncountercursor",
            name="cameras_event_cursor_compat_total",
        ),
        # Expand/contract: поле уходит только из состояния Django, колонка
        # (NULL-able) остаётся для образа автоотката.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="alwaysoncountercursor",
                    name="event_compat_total",
                ),
            ],
            database_operations=[],
        ),
    ]
