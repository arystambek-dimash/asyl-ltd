from rest_framework import serializers
from .models import EventLog


class EventLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventLog
        fields = ["id", "event_type", "message", "user", "order", "payload", "created_at"]
