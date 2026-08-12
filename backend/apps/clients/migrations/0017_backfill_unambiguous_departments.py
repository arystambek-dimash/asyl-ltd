from collections import defaultdict

from django.db import migrations


def backfill_unambiguous_departments(apps, schema_editor):
    """Backfill only when order and assigned-creator evidence agree."""
    Client = apps.get_model("clients", "Client")
    Department = apps.get_model("sales", "Department")
    Employee = apps.get_model("employees", "Employee")
    Order = apps.get_model("orders", "Order")
    database = schema_editor.connection.alias

    if schema_editor.connection.vendor == "postgresql":
        # Freeze both ownership signals for this short migration. PostgreSQL's
        # deferred Order→Client FK does not make a Client row lock sufficient
        # to stop a concurrent legacy writer from inserting another order.
        for model in (Order, Employee, Department):
            table = schema_editor.quote_name(model._meta.db_table)
            schema_editor.execute(f"LOCK TABLE {table} IN SHARE MODE")

    department_ids = dict(
        Department.objects.using(database).values_list("code", "id")
    )
    # Lock every candidate so a concurrent administrator cannot assign it
    # manually between the evidence read and the conditional update.
    unassigned_ids = list(
        Client.objects.using(database)
        .select_for_update()
        .filter(department_id__isnull=True)
        .values_list("id", flat=True)
    )
    codes_by_client = defaultdict(set)
    for client_id, code in (
        Order._base_manager.using(database)
        .filter(client_id__in=unassigned_ids)
        .values_list("client_id", "department")
        .distinct()
    ):
        codes_by_client[client_id].add(code)

    creator_codes_by_client = defaultdict(set)
    for client_id, code in (
        Order._base_manager.using(database)
        .filter(
            client_id__in=unassigned_ids,
            created_by__employee__sales_department_id__isnull=False,
        )
        .values_list(
            "client_id",
            "created_by__employee__sales_department__code",
        )
        .distinct()
    ):
        creator_codes_by_client[client_id].add(code)

    for client_id, codes in codes_by_client.items():
        if len(codes) != 1:
            continue
        code = next(iter(codes))
        # Order.department classifies an individual order, so it is not enough
        # by itself to prove client ownership. Require corroboration from at
        # least one creator whose employee account is assigned to that same
        # department, and reject any conflicting creator assignment.
        if creator_codes_by_client.get(client_id) != {code}:
            continue
        department_id = department_ids.get(code)
        if department_id is None:
            continue
        # Keep a concurrent/manual assignment made during a rolling deploy.
        Client.objects.using(database).filter(
            pk=client_id,
            department_id__isnull=True,
        ).update(department_id=department_id)


class Migration(migrations.Migration):
    dependencies = [
        ("clients", "0016_client_department"),
        ("orders", "0029_performance_indexes"),
    ]

    operations = [
        migrations.RunPython(
            backfill_unambiguous_departments,
            migrations.RunPython.noop,
        ),
    ]
