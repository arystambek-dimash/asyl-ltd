from django.contrib import admin
from .models import Grade, Packaging, Product

admin.site.register([Grade, Packaging, Product])
