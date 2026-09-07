from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def retire_accounts(apps, schema_editor):
    alias = schema_editor.connection.alias
    Device = apps.get_model("cameras", "MonoblockDevice")
    User = apps.get_model("accounts", "User")
    Settings = apps.get_model("cameras", "MonoblockCameraSettings")
    Event = apps.get_model("eventlog", "EventLog")
    devices = list(Device.objects.using(alias).order_by("id"))
    if not devices:
        return
    row, _ = Settings.objects.using(alias).get_or_create(singleton=True)
    sources = list(dict.fromkeys([
        *(row.camera_sources or []),
        *(device.camera_source for device in devices if device.is_active),
    ]))
    row.camera_sources = sources
    row.save(using=alias, update_fields=["camera_sources"])
    for device in devices:
        # Keep the author row for session/payment/audit foreign keys. Revocation
        # also invalidates existing JWT authentication and signed stream access.
        User.objects.using(alias).filter(pk=device.user_id).update(
            is_active=False, password="!retired-monoblock-account",
        )
        Event.objects.using(alias).create(
            event_type="monoblock_device",
            message=f"Технический аккаунт моноблока «{device.name}» отключён",
            user_id=device.user_id,
            payload={"action": "retired", "device_id": device.pk,
                     "camera": device.camera_source, "was_active": device.is_active},
        )
    Device.objects.using(alias).all().update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0030_multi_warehouse_stock_guard"),
        ("eventlog", "0003_eventlog_eventlog_recent_idx_and_more"),
    ]
    operations = [
        # Rolling image rollback must not reactivate revoked technical logins.
        migrations.RunPython(retire_accounts, migrations.RunPython.noop),
        # Retain the dormant table for the previous image's rollback queries.
        # The archive remains managed for referential integrity, but has no API
        # and is never read by authentication or camera policies.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameModel(old_name="MonoblockDevice", new_name="RetiredMonoblockAccount"),
                migrations.AlterModelTable(name="retiredmonoblockaccount", table="cameras_monoblockdevice"),
                migrations.AlterField(model_name="retiredmonoblockaccount", name="user", field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="retired_monoblock_account", to=settings.AUTH_USER_MODEL)),
                migrations.AlterField(model_name="retiredmonoblockaccount", name="created_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_retired_monoblock_accounts", to=settings.AUTH_USER_MODEL)),
            ],
            database_operations=[],
        ),
    ]
