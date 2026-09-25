from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import BotMessageViewSet, WhatsAppBotSettingsView, WhatsAppBotStatusView

router = SimpleRouter()
router.register("bots/whatsapp/messages", BotMessageViewSet, basename="bot-message")

urlpatterns = [
    path("bots/whatsapp/status/", WhatsAppBotStatusView.as_view()),
    path("bots/whatsapp/settings/", WhatsAppBotSettingsView.as_view()),
    *router.urls,
]
