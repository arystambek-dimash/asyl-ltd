from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class SeparateContourMigrationTests(TransactionTestCase):
    migrate_from = [
        ("accounts", "0003_user_must_change_password"),
        ("cameras", "0027_manual_bag_analytics_import"),
    ]
    migrate_to = [
        ("accounts", "0003_user_must_change_password"),
        ("cameras", "0028_separate_shipping_ai247_analytics"),
    ]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        settings_model = old_apps.get_model("cameras", "MonoblockCameraSettings")
        device_model = old_apps.get_model("cameras", "MonoblockDevice")
        daily_model = old_apps.get_model("cameras", "AlwaysOnDailyAnalytics")
        cursor_model = old_apps.get_model("cameras", "AlwaysOnCounterCursor")
        imported_event_model = old_apps.get_model(
            "cameras", "AlwaysOnImportedEvent"
        )
        manual_batch_model = old_apps.get_model(
            "cameras", "ManualBagAnalyticsImportBatch"
        )
        manual_event_model = old_apps.get_model(
            "cameras", "ManualBagAnalyticsImportEvent"
        )
        session_model = old_apps.get_model("cameras", "AiCountingSession")
        user_model = old_apps.get_model("accounts", "User")
        client_model = old_apps.get_model("clients", "Client")
        order_model = old_apps.get_model("orders", "Order")

        settings_model.objects.create(
            singleton=True,
            camera_sources=["cam2", "cam6"],
            always_on_camera_sources=[
                "cam3",
                "cam2",
                "cam5",
                "cam4",
                "cam8",
                "cam9",
            ],
        )
        active_user = user_model.objects.create(username="shipping-migration-active")
        inactive_user = user_model.objects.create(
            username="shipping-migration-inactive"
        )
        device_model.objects.create(
            user=active_user,
            name="Active shipping station",
            camera_source="cam4",
            is_active=True,
        )
        device_model.objects.create(
            user=inactive_user,
            name="Inactive shipping station",
            camera_source="cam5",
            is_active=False,
        )
        session_user = user_model.objects.create(username="migration-session-client")
        session_client = client_model.objects.create(
            user=session_user,
            phone="migration-session",
        )
        session_order = order_model.objects.create(client=session_client)
        session_model.objects.create(
            order=session_order,
            camera="cam8",
            status="active",
        )
        session_model.objects.create(
            order=session_order,
            camera="cam9",
            status="closed",
        )
        cursor_model.objects.create(camera="cam7", last_total=0)
        imported_event_model.objects.create(
            camera="cam7",
            upstream_event_id=1,
            occurred_at=timezone.now(),
            source="sub",
            mode="always_on",
            applied_to_analytics=True,
        )
        imported_event_model.objects.create(
            camera="cam11",
            upstream_event_id=30815,
            occurred_at=timezone.now(),
            source="sub",
            mode="always_on",
            applied_to_analytics=False,
        )
        captured_at = timezone.now()
        manual_batch = manual_batch_model.objects.create(
            file_sha256="a" * 64,
            schema_name="asyl.best_pt_manual_bag_events.v1",
            source_filename="production-recovery.json",
            model_id="best.pt",
            model_sha256="b" * 64,
            camera="cam10",
            source="sub",
            analytics_scope="ai_247",
            event_count=1,
            first_captured_at=captured_at,
            last_captured_at=captured_at,
            per_day={str(timezone.localdate()): {"total": 1}},
        )
        manual_event_model.objects.create(
            batch=manual_batch,
            idempotency_key="migration-preserves-manual-ledger",
            sequence=1,
            captured_at=captured_at,
            local_day=timezone.localdate(),
            camera="cam10",
            source="sub",
            model_event_origin="production",
            source_row_id=30815,
            class_name="Red",
            color="red",
            classification_status="classified",
        )

        self.day = timezone.localdate()
        self.legacy_values = {
            "model_total": 11,
            "model_per_color": {"red": 8, "blue": 3},
            "model_per_brand": {"korol": 7, "unknown": 4},
            "adjustment": -2,
        }
        daily_model.objects.create(
            camera="cam2",
            day=self.day,
            **self.legacy_values,
        )
        daily_model.objects.create(camera="cam4", day=self.day, model_total=4)
        daily_model.objects.create(camera="cam5", day=self.day, model_total=5)
        daily_model.objects.create(
            camera="cam6",
            day=self.day,
            model_total=6,
            archived_at=timezone.now(),
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_history_is_deferred_without_moving_rollback_owned_rows(self):
        legacy_model = self.apps.get_model("cameras", "AlwaysOnDailyAnalytics")
        shipping_model = self.apps.get_model("cameras", "ShippingDailyAnalytics")
        bootstrap_model = self.apps.get_model(
            "cameras",
            "ShippingAnalyticsBootstrap",
        )

        # No static copy is safe here: events can enter the legacy ledger
        # until CV confirms the role-aware policy. The monitor completes these
        # markers additively after the v2 journal reaches its tail.
        assert not shipping_model.objects.exists()
        assert set(bootstrap_model.objects.values_list("camera", flat=True)) == {
            "cam2",
            "cam4",
            "cam6",
            "cam8",
        }

        # This is deliberately a copy, not a move. The rollback image still
        # owns this table and must see the pre-deploy history unchanged.
        assert legacy_model.objects.filter(
            camera__in=["cam2", "cam4", "cam5", "cam6"],
            day=self.day,
        ).count() == 4
        original = legacy_model.objects.get(camera="cam2", day=self.day)
        for field, expected in self.legacy_values.items():
            assert getattr(original, field) == expected

    def test_legacy_role_overlap_is_removed_with_shipping_precedence(self):
        settings_model = self.apps.get_model(
            "cameras",
            "MonoblockCameraSettings",
        )

        row = settings_model.objects.get(singleton=True)
        # cam2 comes from the shipping picker and cam4 from an active device;
        # both leave the explicit AI-24/7 list, while order is preserved.
        assert row.always_on_camera_sources == ["cam3", "cam5", "cam9"]

    def test_permanent_roles_preserve_current_and_dormant_ownership(self):
        role_model = self.apps.get_model("cameras", "ContinuousCameraRole")

        roles = dict(
            role_model.objects.values_list("camera", "analytics_scope")
        )
        assert roles == {
            "cam2": "shipping",
            "cam3": "ai_247",
            "cam4": "shipping",
            # Inactive device alone does not override explicit current AI247.
            "cam5": "ai_247",
            "cam6": "shipping",
            # Only an open historical session is current shipping evidence.
            "cam8": "shipping",
            "cam9": "ai_247",
            # A dormant cursor reserves AI ownership without reactivation.
            "cam7": "ai_247",
            # A manual analytics ledger is explicit historical AI ownership.
            "cam10": "ai_247",
        }

    def test_legacy_event_state_and_old_image_defaults_remain_safe(self):
        executor = MigrationExecutor(connection)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        old_event_model = old_apps.get_model(
            "cameras", "AlwaysOnImportedEvent"
        )
        old_event_model.objects.create(
            camera="cam7",
            upstream_event_id=2,
            occurred_at=timezone.now(),
            source="sub",
            mode="always_on",
            applied_to_analytics=True,
        )

        new_event_model = self.apps.get_model(
            "cameras", "AlwaysOnImportedEvent"
        )
        migrated = new_event_model.objects.get(
            camera="cam7",
            upstream_event_id=1,
        )
        rollback_insert = new_event_model.objects.get(
            camera="cam7",
            upstream_event_id=2,
        )
        for event in (migrated, rollback_insert):
            assert event.analytics_scope == "ai_247"
            assert event.applied_to_production is True
            assert event.applied_to_shipping_bootstrap is False

        skipped = new_event_model.objects.get(
            camera="cam11",
            upstream_event_id=30815,
        )
        assert skipped.analytics_scope == "ai_247"
        assert skipped.applied_to_analytics is False
        assert skipped.applied_to_production is False
        assert skipped.applied_to_shipping_bootstrap is False

    def test_manual_import_audit_ledger_survives_upgrade(self):
        batch_model = self.apps.get_model(
            "cameras", "ManualBagAnalyticsImportBatch"
        )
        event_model = self.apps.get_model(
            "cameras", "ManualBagAnalyticsImportEvent"
        )

        batch = batch_model.objects.get(file_sha256="a" * 64)
        event = event_model.objects.get(batch=batch)
        assert batch.event_count == 1
        assert batch.camera == "cam10"
        assert event.idempotency_key == "migration-preserves-manual-ledger"
        assert event.camera == "cam10"
        assert event.source_row_id == 30815

    def test_old_orm_remains_unambiguous_after_both_contours_write_same_day(self):
        legacy_model = self.apps.get_model("cameras", "AlwaysOnDailyAnalytics")
        shipping_model = self.apps.get_model("cameras", "ShippingDailyAnalytics")

        # Candidate code can independently update both ledgers for one
        # physical camera/day without weakening the legacy unique key.
        legacy_model.objects.filter(camera="cam2", day=self.day).update(
            model_total=12
        )
        shipping_model.objects.create(
            camera="cam2",
            day=self.day,
            model_total=13,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        old_daily_model = old_apps.get_model(
            "cameras",
            "AlwaysOnDailyAnalytics",
        )
        old_batch_model = old_apps.get_model(
            "cameras",
            "ManualBagAnalyticsImportBatch",
        )
        old_event_model = old_apps.get_model(
            "cameras",
            "ManualBagAnalyticsImportEvent",
        )

        row, created = old_daily_model.objects.get_or_create(
            camera="cam2",
            day=self.day,
        )
        assert created is False
        assert row.model_total == 12
        assert old_daily_model.objects.filter(
            camera="cam2",
            day=self.day,
        ).count() == 1
        batch = old_batch_model.objects.get(file_sha256="a" * 64)
        event = old_event_model.objects.get(batch=batch)
        assert batch.event_count == 1
        assert batch.camera == "cam10"
        assert event.idempotency_key == "migration-preserves-manual-ledger"
        assert event.source_row_id == 30815
