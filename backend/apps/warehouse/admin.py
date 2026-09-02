from django.contrib import admin

from apps.common.admin import ReadOnlyOperationalAdmin

from .models import StockItem, StockMovement, StockReceipt, Warehouse

admin.site.register(
    [Warehouse, StockItem, StockReceipt],
    ReadOnlyOperationalAdmin,
)


@admin.register(StockMovement)
class StockMovementAdmin(ReadOnlyOperationalAdmin):
    list_display = (
        "product",
        "delta",
        "balance_after",
        "reason",
        "created_at",
        "created_by",
    )
    list_filter = ("reason", "product")
    readonly_fields = (
        "product",
        "delta",
        "balance_after",
        "reason",
        "note",
        "created_at",
        "created_by",
    )
