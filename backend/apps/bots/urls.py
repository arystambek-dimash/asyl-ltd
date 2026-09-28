from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import BotMessageViewSet, TelegramBotSettingsView, TelegramBotStatusView

router = SimpleRouter()
router.register("bots/telegram/messages", BotMessageViewSet, basename="bot-message")

urlpatterns = [
    path("bots/telegram/status/", TelegramBotStatusView.as_view()),
    path("bots/telegram/settings/", TelegramBotSettingsView.as_view()),
    *router.urls,
]
