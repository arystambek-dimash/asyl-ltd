from django.db import migrations, models


def mark_shipping_history_for_bootstrap(apps, schema_editor):
    """Fence initial shipping cameras for a post-policy additive seed.

    Copying here would race the first role-aware CV policy PUT: legacy-scope
    events can still reach the AI table after this transaction. The monitor
    completes these markers only after CV confirms ``shipping`` and its v2
    journal is caught up, so the copied legacy total includes that final tail.
    """

    database = schema_editor.connection.alias
    settings_model = apps.get_model("cameras", "MonoblockCameraSettings")
    device_model = apps.get_model("cameras", "MonoblockDevice")
    session_model = apps.get_model("cameras", "AiCountingSession")
    bootstrap_model = apps.get_model("cameras", "ShippingAnalyticsBootstrap")

    row = settings_model.objects.using(database).filter(singleton=True).first()
    configured_sources = (
        row.camera_sources
        if row is not None and isinstance(row.camera_sources, list)
        else []
    )
    sources = {
        source
        for source in configured_sources
        if isinstance(source, str) and source
    }
    sources.update(
        device_model.objects.using(database)
        .filter(is_active=True)
        .values_list("camera_source", flat=True)
    )
    sources.update(
        session_model.objects.using(database)
        .filter(status__in=("starting", "active"))
        .values_list("camera", flat=True)
    )
    bootstrap_model.objects.using(database).bulk_create(
        [bootstrap_model(camera=source) for source in sorted(sources)],
        ignore_conflicts=True,
    )


def make_continuous_roles_disjoint(apps, schema_editor):
    """Resolve legacy overlap deterministically: the shipping role wins.

    Before contours were separate, shipping cameras were intentionally also
    present in the effective AI-24/7 union. Some installations additionally
    persisted the same camera in ``always_on_camera_sources``. Leaving that
    duplicate in the explicit AI list would make the new role map impossible
    to construct immediately after deploy.
    """

    database = schema_editor.connection.alias
    settings_model = apps.get_model("cameras", "MonoblockCameraSettings")
    device_model = apps.get_model("cameras", "MonoblockDevice")
    session_model = apps.get_model("cameras", "AiCountingSession")
    row = settings_model.objects.using(database).filter(singleton=True).first()
    if row is None or not isinstance(row.always_on_camera_sources, list):
        return

    configured_sources = (
        row.camera_sources if isinstance(row.camera_sources, list) else []
    )
    shipping_sources = {
        source
        for source in configured_sources
        if isinstance(source, str) and source
    }
    shipping_sources.update(
        device_model.objects.using(database)
        .filter(is_active=True)
        .values_list("camera_source", flat=True)
    )
    shipping_sources.update(
        session_model.objects.using(database)
        .filter(status__in=("starting", "active"))
        .values_list("camera", flat=True)
    )
    ai247_sources = [
        source
        for source in row.always_on_camera_sources
        if source not in shipping_sources
    ]
    if ai247_sources != row.always_on_camera_sources:
        settings_model.objects.using(database).filter(pk=row.pk).update(
            always_on_camera_sources=ai247_sources
        )


def seed_continuous_camera_roles(apps, schema_editor):
    """Reserve every active camera's migration-time business contour.

    Shipping wins legacy overlap. Inactive reservations remain in the table
    forever after their first candidate-era assignment, but a pre-feature
    inactive device did not yet own a running analytics contour and therefore
    is not seeded unless its camera was explicitly in the AI 24/7 list.
    """

    database = schema_editor.connection.alias
    settings_model = apps.get_model("cameras", "MonoblockCameraSettings")
    device_model = apps.get_model("cameras", "MonoblockDevice")
    session_model = apps.get_model("cameras", "AiCountingSession")
    role_model = apps.get_model("cameras", "ContinuousCameraRole")
    cursor_model = apps.get_model("cameras", "AlwaysOnCounterCursor")
    daily_model = apps.get_model("cameras", "AlwaysOnDailyAnalytics")
    imported_event_model = apps.get_model("cameras", "AlwaysOnImportedEvent")
    archive_model = apps.get_model("cameras", "AlwaysOnCountArchive")
    mapping_model = apps.get_model("cameras", "AlwaysOnColorProductMapping")
    production_run_model = apps.get_model("cameras", "AlwaysOnProductionRun")
    production_correction_model = apps.get_model(
        "cameras", "AlwaysOnProductionCorrection"
    )
    stock_batch_model = apps.get_model("cameras", "AlwaysOnStockBatch")
    row = settings_model.objects.using(database).filter(singleton=True).first()
    configured_shipping = (
        row.camera_sources
        if row is not None and isinstance(row.camera_sources, list)
        else []
    )
    shipping_sources = {
        source
        for source in configured_shipping
        if isinstance(source, str) and source
    }
    shipping_sources.update(
        device_model.objects.using(database)
        .filter(is_active=True)
        .values_list("camera_source", flat=True)
    )
    shipping_sources.update(
        session_model.objects.using(database)
        .filter(status__in=("starting", "active"))
        .values_list("camera", flat=True)
    )
    configured_ai247 = (
        row.always_on_camera_sources
        if row is not None and isinstance(row.always_on_camera_sources, list)
        else []
    )
    ai247_sources = {
        source
        for source in configured_ai247
        if isinstance(source, str) and source not in shipping_sources
    }
    # Dormant legacy artifacts reserve ownership without reactivating a
    # processor. This lets pending drains and stock/archive controls finish,
    # while current shipping still wins every pre-feature overlap.
    historical_ai_sources = set(
        cursor_model.objects.using(database)
        .order_by()
        .values_list("camera", flat=True)
        .distinct()
    )
    historical_ai_sources.update(
        daily_model.objects.using(database)
        .order_by()
        .values_list("camera", flat=True)
        .distinct()
    )
    historical_ai_sources.update(
        imported_event_model.objects.using(database)
        .filter(analytics_scope="ai_247")
        .order_by()
        .values_list("camera", flat=True)
        .distinct()
    )
    historical_ai_sources.update(
        archive_model.objects.using(database)
        .order_by()
        .values_list("camera", flat=True)
        .distinct()
    )
    historical_ai_sources.update(
        mapping_model.objects.using(database)
        .order_by()
        .values_list("camera", flat=True)
        .distinct()
    )
    historical_ai_sources.update(
        production_run_model.objects.using(database)
        .order_by()
        .values_list("camera", flat=True)
        .distinct()
    )
    historical_ai_sources.update(
        production_correction_model.objects.using(database)
        .order_by()
        .values_list("camera", flat=True)
        .distinct()
    )
    historical_ai_sources.update(
        stock_batch_model.objects.using(database)
        .order_by()
        .values_list("camera", flat=True)
        .distinct()
    )
    ai247_sources.update(historical_ai_sources - shipping_sources)
    role_model.objects.using(database).bulk_create(
        [
            *(
                role_model(camera=source, analytics_scope="shipping")
                for source in sorted(shipping_sources)
            ),
            *(
                role_model(camera=source, analytics_scope="ai_247")
                for source in sorted(ai247_sources)
            ),
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0027_manual_bag_analytics_import"),
    ]

    operations = [
        migrations.AddField(
            model_name="alwaysonimportedevent",
            name="analytics_scope",
            field=models.CharField(
                db_default="ai_247",
                default="ai_247",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="alwaysonimportedevent",
            name="applied_to_production",
            field=models.BooleanField(db_default=True, default=False),
        ),
        migrations.AddField(
            model_name="alwaysonimportedevent",
            name="applied_to_shipping_bootstrap",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.CreateModel(
            name="ShippingDailyAnalytics",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("camera", models.CharField(max_length=32)),
                ("day", models.DateField(db_index=True)),
                ("model_total", models.PositiveIntegerField(default=0)),
                ("model_per_color", models.JSONField(blank=True, default=dict)),
                (
                    "model_per_brand",
                    models.JSONField(blank=True, db_default={}, default=dict),
                ),
                ("adjustment", models.IntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["camera"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("camera", "day"),
                        name="cameras_one_shipping_total_per_day",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ShippingAnalyticsBootstrap",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("camera", models.CharField(max_length=32, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "scope_confirmed_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["camera"]},
        ),
        migrations.CreateModel(
            name="ContinuousCameraRole",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("camera", models.CharField(max_length=32, unique=True)),
                (
                    "analytics_scope",
                    models.CharField(
                        choices=[
                            ("shipping", "Shipping"),
                            ("ai_247", "AI 24/7"),
                        ],
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.RunPython(
            mark_shipping_history_for_bootstrap,
            migrations.RunPython.noop,
        ),
        migrations.RunPython(
            make_continuous_roles_disjoint,
            migrations.RunPython.noop,
        ),
        migrations.RunPython(
            seed_continuous_camera_roles,
            migrations.RunPython.noop,
        ),
    ]
