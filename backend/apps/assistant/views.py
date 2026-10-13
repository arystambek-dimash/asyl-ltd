import json
from itertools import chain

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import HasPerm
from apps.common.viewsets import NoStoreMixin

from . import services
from .models import AssistantChat
from .serializers import (
    AskSerializer,
    AssistantChatDetailSerializer,
    AssistantChatSerializer,
    AssistantMessageSerializer,
)

PERMISSION = "assistant.view"
_RECENT_CHATS = 50
_BUSY_TEXTS = {
    "assistant_user_busy": "Дождитесь ответа на предыдущий вопрос.",
    "assistant_busy": "Ассистент сейчас отвечает другим сотрудникам. Повторите через минуту.",
}


class AssistantChatViewSet(
    NoStoreMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Свои разговоры с ассистентом: список, переписка, удаление."""

    pagination_class = None

    def get_permissions(self):
        return [HasPerm(PERMISSION)]

    def get_queryset(self):
        chats = AssistantChat.objects.filter(user=self.request.user)
        if self.action == "retrieve":
            return chats.prefetch_related("messages")
        return chats

    def list(self, request, *args, **kwargs):
        chats = self.get_queryset()[:_RECENT_CHATS]
        return Response(AssistantChatSerializer(chats, many=True).data)

    def get_serializer_class(self):
        return AssistantChatDetailSerializer if self.action == "retrieve" else AssistantChatSerializer


def _ndjson(events):
    for event in events:
        if event["type"] == "reply":
            event = {"type": "reply", "message": AssistantMessageSerializer(event["message"]).data}
        yield json.dumps(event, ensure_ascii=False, default=str) + "\n"


class AssistantAskView(NoStoreMixin, APIView):
    """Вопрос ассистенту. Ответ — поток NDJSON: start, шаги step, итоговый reply."""

    def get_permissions(self):
        return [HasPerm(PERMISSION)]

    def post(self, request):
        if not services.enabled():
            return Response(
                {"detail": "ИИ-ассистент не настроен: нет ключа OpenAI.", "code": "assistant_unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        serializer = AskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chat_id = serializer.validated_data.get("chat")
        chat = get_object_or_404(AssistantChat, pk=chat_id, user=request.user) if chat_id else None
        if services.questions_left_today(request.user) <= 0:
            return Response(
                {"detail": "Лимит вопросов на сегодня исчерпан.", "code": "assistant_daily_limit"},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        try:
            slot = services.claim_answer_slot(request.user)
        except services.AssistantBusy as exc:
            return Response(
                {"detail": _BUSY_TEXTS[exc.code], "code": exc.code}, status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        try:
            chat, question = services.start_question(request.user, serializer.validated_data["question"], chat)
        except BaseException:
            slot.release()
            raise
        start = {
            "type": "start",
            "chat": AssistantChatSerializer(chat).data,
            "question": AssistantMessageSerializer(question).data,
        }
        events = services.answer_events(request.user, chat, question, slot)
        response = StreamingHttpResponse(
            _ndjson(chain([start], events)), content_type="application/x-ndjson; charset=utf-8",
        )
        # nginx не копит поток: шаги видны сразу, а не одним куском в конце.
        response["X-Accel-Buffering"] = "no"
        return response
