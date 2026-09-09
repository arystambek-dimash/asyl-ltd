"""Remove the old portal default only from untouched, unconfirmed requests."""

from django.db import migrations


def clear_legacy_portal_department(apps, schema_editor):
    alias = schema_editor.connection.alias
    Order = apps.get_model("orders", "Order")
    Department = apps.get_model("sales", "Department")
    EventLog = apps.get_model("eventlog", "EventLog")
    departments = Department.objects.using(alias).filter(is_active=True)
    default = departments.filter(is_default=True).first() or departments.first()
    default_code = default.code if default else "main"
    # The old portal did not set created_by and never let the client choose
    # a department. Staff-created requests and any manually edited / previously
    # confirmed request must keep their explicit assignment.
    candidates = (
        Order.objects.using(alias)
        .select_for_update()
        .filter(
            status="pending",
            department=default_code,
            client__department__isnull=True,
            created_by__isnull=True,
            repeated_from__isnull=True,
            settlement_intent="pending",
            payment_method="pending",
            deleted_at__isnull=True,
        )
        .exclude(
            events__event_type__in=["status", "order_edit", "order_confirm", "shipment"]
        )
        .exclude(payments__isnull=False)
    )
    for order in candidates.iterator():
        Order.objects.using(alias).filter(pk=order.pk).update(department="")
        EventLog.objects.using(alias).create(
            order_id=order.pk,
            event_type="order_department_cleanup",
            message="Убрана автоматическая подстановка отдела у заявки без отдела клиента",
            payload={"from": default_code, "to": "", "reason": "legacy_portal_default"},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0035_order_review_workflow"),
        ("clients", "0016_client_department"),
        ("sales", "0001_initial"),
        ("eventlog", "0003_eventlog_eventlog_recent_idx_and_more"),
    ]
    operations = [
        migrations.RunPython(clear_legacy_portal_department, migrations.RunPython.noop),
    ]
