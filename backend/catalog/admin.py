from django.contrib import admin
from .models import Grade, Packaging, Product


@admin.register(Grade)
class GradeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_editable = ("is_active",)
    search_fields = ("name",)


@admin.register(Packaging)
class PackagingAdmin(admin.ModelAdmin):
    list_display = ("name", "weight_kg", "is_active")
    list_editable = ("is_active",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("__str__", "grade", "packaging", "price", "is_active")
    list_filter = ("grade", "packaging", "is_active")
    list_editable = ("price", "is_active")
    autocomplete_fields = ("grade",)
