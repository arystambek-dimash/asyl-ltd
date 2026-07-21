from rest_framework import serializers
from .models import EventLog


class EventLogSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.username", read_only=True, allow_null=True)

    class Meta:
        model = EventLog
        fields = ["id", "event_type", "message", "user", "user_name", "order", "payload", "created_at"]
