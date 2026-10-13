from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import AssistantAskView, AssistantChatViewSet

router = SimpleRouter()
router.register("assistant/chats", AssistantChatViewSet, basename="assistant-chat")

urlpatterns = [
    path("assistant/ask/", AssistantAskView.as_view(), name="assistant-ask"),
    path("", include(router.urls)),
]
