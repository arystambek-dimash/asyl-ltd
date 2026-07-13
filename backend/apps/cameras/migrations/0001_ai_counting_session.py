# Generated manually for the durable, globally exclusive AI counting slot.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("orders", "0010_payment_chain_backfill"),
    ]

    operations = [
        migrations.CreateModel(
            name="AiCountingSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("singleton", models.BooleanField(default=True, editable=False)),
                ("camera", models.CharField(max_length=32)),
                ("status", models.CharField(default="starting", max_length=12)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("final_total", models.PositiveIntegerField(blank=True, null=True)),
                ("last_status", models.JSONField(blank=True, default=dict)),
                ("error", models.CharField(blank=True, default="", max_length=500)),
                ("closed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ai_counting_sessions_closed", to=settings.AUTH_USER_MODEL)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="ai_counting_sessions", to="orders.order")),
                ("started_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ai_counting_sessions_started", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.AddConstraint(
            model_name="aicountingsession",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ["starting", "active"])),
                fields=("singleton",),
                name="cameras_one_open_ai_counting_session",
            ),
        ),
    ]

