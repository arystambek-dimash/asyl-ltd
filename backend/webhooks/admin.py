from django.contrib import admin
from .models import Camera, WebhookCall, VideoJob

admin.site.register([Camera, WebhookCall, VideoJob])
