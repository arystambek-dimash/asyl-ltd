from django.contrib import admin


class ReadOnlyOperationalAdmin(admin.ModelAdmin):
    """Operational aggregates are mutated only through audited domain APIs."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
