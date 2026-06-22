from django.contrib import admin
from .models import Camera, WebhookCall, CountSession

admin.site.register([Camera, WebhookCall, CountSession])
