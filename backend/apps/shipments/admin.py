from django.contrib import admin
from apps.common.admin import ReadOnlyOperationalAdmin
from .models import Shipment


admin.site.register(Shipment, ReadOnlyOperationalAdmin)
