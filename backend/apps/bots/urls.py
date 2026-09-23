from django.urls import path

from .views import BotMessageViewSet, WhatsAppBotSettingsView, WhatsAppBotStatusView

# Разбор сообщения: путь → (метод, действие журнала).
MESSAGE_ACTIONS = {
    "preview": ("post", "preview"),
    "options": ("get", "rail_options"),
    "product-codes": ("post", "product_codes"),
    "client-names": ("post", "client_names"),
    "apply": ("post", "apply"),
    "ignore": ("post", "ignore"),
}

urlpatterns = [
    path("bots/whatsapp/status/", WhatsAppBotStatusView.as_view()),
    path("bots/whatsapp/settings/", WhatsAppBotSettingsView.as_view()),
    path("bots/whatsapp/messages/", BotMessageViewSet.as_view({"get": "list"})),
    path("bots/whatsapp/messages/<int:pk>/", BotMessageViewSet.as_view({"get": "retrieve"})),
    *(
        path(f"bots/whatsapp/messages/<int:pk>/{segment}/", BotMessageViewSet.as_view({method: name}))
        for segment, (method, name) in MESSAGE_ACTIONS.items()
    ),
]
