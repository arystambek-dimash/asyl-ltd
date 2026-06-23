from django.db import migrations


CAMERA_PERMISSION_CODES = ["cameras.view", "cameras.manage"]


def remove_camera_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.filter(code__in=CAMERA_PERMISSION_CODES).delete()


class Migration(migrations.Migration):
    dependencies = [("rbac", "0003_add_rbac_permissions")]
    operations = [migrations.RunPython(remove_camera_permissions, migrations.RunPython.noop)]
