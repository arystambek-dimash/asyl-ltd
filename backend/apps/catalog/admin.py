from django.contrib import admin
from .models import Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "color", "weight_kg", "is_active")
    list_filter = ("color", "weight_kg", "is_active")
    list_editable = ("is_active",)
    search_fields = ("name",)
