from django.contrib import admin
from .models import Order, OrderItem, Payment

admin.site.register([Order, OrderItem, Payment])
