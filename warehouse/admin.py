from django.contrib import admin
from .models import StockItem, StockReceipt

admin.site.register([StockItem, StockReceipt])
