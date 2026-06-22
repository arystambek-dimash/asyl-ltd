from django.contrib import admin
from .models import Camera, WebhookCall

admin.site.register([Camera, WebhookCall])
