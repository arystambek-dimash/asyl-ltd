from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class SeparateContourMigrationTests(TransactionTestCase):
    migrate_from = [
        ("accounts", "0003_user_must_change_password"),
        ("cameras", "0026_imported_event_continuous_analytics"),
    ]
    migrate_to = [
        ("accounts", "0003_user_must_change_password"),
        ("cameras", "0027_separate_shipping_ai247_analytics"),
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
        }

    def test_old_image_event_inserts_receive_safe_database_defaults(self):
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
