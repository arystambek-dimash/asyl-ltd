from django.contrib import admin
from apps.common.admin import ReadOnlyOperationalAdmin
from .models import Client


admin.site.register(Client, ReadOnlyOperationalAdmin)
