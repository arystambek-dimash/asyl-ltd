from django.contrib import admin
from .models import Camera, WebhookCall, CountSession, VideoJob

admin.site.register([Camera, WebhookCall, CountSession, VideoJob])
