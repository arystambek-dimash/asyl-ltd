from django.db import migrations, models
from django.db.models import Count, Q


def clear_duplicate_active_camera_bindings(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    AiCountingSession = apps.get_model("cameras", "AiCountingSession")

    active = Order.objects.filter(
        status__in=("confirmed", "arrived", "loading"),
        deleted_at__isnull=True,
    ).exclude(loading_camera="")
    duplicates = (
        active.values("loading_camera")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for row in duplicates.iterator():
        camera = row["loading_camera"]
        order_ids = list(
            active.filter(loading_camera=camera)
            .order_by("id")
            .values_list("id", flat=True)
        )
        session_owner = (
            AiCountingSession.objects.filter(
                camera=camera,
                status__in=("starting", "active"),
                order_id__in=order_ids,
            )
            .order_by("started_at")
            .values_list("order_id", flat=True)
            .first()
        )
        bound = active.filter(loading_camera=camera)
        # Реальную открытую AI-сессию считаем единственным источником истины.
        # Если её нет, все конфликтующие старые подписи были «залипшими» —
        # очищаем их, чтобы оператор заново выбрал владельца камеры.
        if session_owner:
            bound.exclude(pk=session_owner).update(loading_camera="")
        else:
            bound.update(loading_camera="")


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0007_shipping_history_settings"),
        ("orders", "0018_remove_payment_accountant_at_and_more"),
    ]

    operations = [
        migrations.RunPython(
            clear_duplicate_active_camera_bindings,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="order",
            constraint=models.UniqueConstraint(
                fields=("loading_camera",),
                condition=(
                    ~Q(loading_camera="")
                    & Q(status__in=["confirmed", "arrived", "loading"])
                    & Q(deleted_at__isnull=True)
                ),
                name="orders_one_active_order_per_loading_camera",
            ),
        ),
    ]
