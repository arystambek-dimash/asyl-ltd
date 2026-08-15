from django.contrib import admin

from apps.common.admin import ReadOnlyOperationalAdmin

from .models import Order, OrderItem, Payment

admin.site.register(Order, ReadOnlyOperationalAdmin)
admin.site.register(OrderItem, ReadOnlyOperationalAdmin)
admin.site.register(Payment, ReadOnlyOperationalAdmin)
