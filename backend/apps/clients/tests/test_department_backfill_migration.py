from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

MIGRATE_FROM = [
    ("clients", "0016_client_department"),
    ("orders", "0029_performance_indexes"),
]
MIGRATE_TO = [("clients", "0017_backfill_unambiguous_departments")]


class ClientDepartmentBackfillMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_FROM)
        old_apps = executor.loader.project_state(MIGRATE_FROM).apps

        User = old_apps.get_model("accounts", "User")
        Client = old_apps.get_model("clients", "Client")
        Department = old_apps.get_model("sales", "Department")
        Employee = old_apps.get_model("employees", "Employee")
        Order = old_apps.get_model("orders", "Order")

        main, _ = Department.objects.get_or_create(
            code="main",
            defaults={"name": "Основной"},
        )
        field, _ = Department.objects.get_or_create(
            code="field",
            defaults={"name": "Поле"},
        )
        self.main_id = main.pk
        self.field_id = field.pk

        main_user = User.objects.create(username="main-creator", is_staff=True)
        field_user = User.objects.create(username="field-creator", is_staff=True)
        Employee.objects.create(user=main_user, sales_department=main)
        Employee.objects.create(user=field_user, sales_department=field)

        def make_client(username, *, department=None):
            user = User.objects.create(
                username=username,
                first_name=username,
                is_client=True,
            )
            return Client.objects.create(
                user=user,
                phone=username,
                department=department,
            )

        unambiguous = make_client("unambiguous")
        Order.objects.create(
            client=unambiguous,
            department="main",
            created_by=main_user,
        )
        Order.objects.create(
            client=unambiguous,
            department="main",
            created_by=main_user,
            deleted_at=timezone.now(),
        )

        deleted_only = make_client("deleted-only")
        Order.objects.create(
            client=deleted_only,
            department="field",
            created_by=field_user,
            deleted_at=timezone.now(),
        )

        multiple = make_client("multiple")
        Order.objects.create(
            client=multiple,
            department="main",
            created_by=main_user,
        )
        Order.objects.create(
            client=multiple,
            department="field",
            created_by=field_user,
            deleted_at=timezone.now(),
        )

        no_orders = make_client("no-orders")
        no_creator_evidence = make_client("no-creator-evidence")
        Order.objects.create(client=no_creator_evidence, department="main")
        conflicting_creator = make_client("conflicting-creator")
        Order.objects.create(
            client=conflicting_creator,
            department="main",
            created_by=field_user,
        )
        unknown = make_client("unknown")
        Order.objects.create(
            client=unknown,
            department="missing-code",
            created_by=main_user,
        )
        assigned = make_client("assigned", department=field)
        Order.objects.create(
            client=assigned,
            department="main",
            created_by=main_user,
        )

        self.client_ids = {
            "unambiguous": unambiguous.pk,
            "deleted_only": deleted_only.pk,
            "multiple": multiple.pk,
            "no_orders": no_orders.pk,
            "no_creator_evidence": no_creator_evidence.pk,
            "conflicting_creator": conflicting_creator.pk,
            "unknown": unknown.pk,
            "assigned": assigned.pk,
        }

        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_TO)
        self.apps = executor.loader.project_state(MIGRATE_TO).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_only_unambiguous_known_order_histories_are_backfilled(self):
        Client = self.apps.get_model("clients", "Client")

        assert Client.objects.get(
            pk=self.client_ids["unambiguous"]
        ).department_id == self.main_id
        assert Client.objects.get(
            pk=self.client_ids["deleted_only"]
        ).department_id == self.field_id
        assert Client.objects.get(
            pk=self.client_ids["multiple"]
        ).department_id is None
        assert Client.objects.get(
            pk=self.client_ids["no_orders"]
        ).department_id is None
        assert Client.objects.get(
            pk=self.client_ids["no_creator_evidence"]
        ).department_id is None
        assert Client.objects.get(
            pk=self.client_ids["conflicting_creator"]
        ).department_id is None
        assert Client.objects.get(
            pk=self.client_ids["unknown"]
        ).department_id is None
        assert Client.objects.get(
            pk=self.client_ids["assigned"]
        ).department_id == self.field_id

    def test_reverse_is_noop_and_preserves_later_assignments(self):
        Client = self.apps.get_model("clients", "Client")
        client = Client.objects.get(pk=self.client_ids["unambiguous"])
        client.department_id = self.field_id
        client.save(update_fields=["department"])

        executor = MigrationExecutor(connection)
        executor.migrate(MIGRATE_FROM)
        old_apps = executor.loader.project_state(MIGRATE_FROM).apps
        OldClient = old_apps.get_model("clients", "Client")

        assert OldClient.objects.get(pk=client.pk).department_id == self.field_id
