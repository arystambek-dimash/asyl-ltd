from django.contrib import admin
from .models import StockItem, StockReceipt, StockMovement

admin.site.register([StockItem, StockReceipt])


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("product", "delta", "balance_after", "reason", "created_at", "created_by")
    list_filter = ("reason", "product")
    readonly_fields = ("product", "delta", "balance_after", "reason", "note",
                       "created_at", "created_by")
