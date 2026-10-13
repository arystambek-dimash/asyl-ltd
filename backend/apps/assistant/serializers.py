from rest_framework import serializers

from . import schema
from .models import AssistantChat, AssistantMessage


class AssistantMessageSerializer(serializers.ModelSerializer):
    # Карта данных ответа для повтора: таблицы из шагов и связи между ними.
    data_map = serializers.SerializerMethodField()

    class Meta:
        model = AssistantMessage
        fields = ["id", "role", "content", "table", "trace", "stats", "data_map", "error", "created_at"]

    def get_data_map(self, message):
        return schema.data_map([name for step in message.trace for name in step.get("tables") or []])


class AssistantChatSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssistantChat
        fields = ["id", "title", "updated_at"]


class AssistantChatDetailSerializer(AssistantChatSerializer):
    messages = AssistantMessageSerializer(many=True, read_only=True)

    class Meta(AssistantChatSerializer.Meta):
        fields = [*AssistantChatSerializer.Meta.fields, "messages"]


class AskSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=2000, trim_whitespace=True)
    chat = serializers.IntegerField(required=False, allow_null=True)
