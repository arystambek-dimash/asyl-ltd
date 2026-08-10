from django.apps import AppConfig


class SysPermissionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sys_permissions"
    # Keep the historical Django identity so existing migration records,
    # content types, table names and foreign keys remain valid in production.
    label = "rbac"
    verbose_name = "Системные права"
