from rest_framework import serializers
from .models import Camera, WebhookCall, VideoJob


class CameraSerializer(serializers.ModelSerializer):
    api_key = serializers.SerializerMethodField()

    class Meta:
        model = Camera
        fields = ["id", "name", "camera_id", "kind", "status", "api_key",
                  "response_template", "is_active", "last_seen"]

    def get_api_key(self, obj):
        if self.context.get("reveal_key"):
            return obj.api_key
        tail = obj.api_key[-4:] if obj.api_key else ""
        return f"••••{tail}"

    def create(self, validated_data):
        validated_data["api_key"] = Camera.generate_key()
        return super().create(validated_data)


class WebhookCallSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookCall
        fields = ["id", "camera", "plate", "payload_bags", "payload_weight",
                  "matched_order", "decision", "reason", "created_at"]


class VideoJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoJob
        fields = ["id", "order", "status", "bags_counted", "counts_by_class",
                  "error", "video", "created_at", "finished_at"]
