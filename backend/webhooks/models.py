import secrets
from django.db import models


class Camera(models.Model):
    KINDS = [("entry", "Въезд"), ("counter", "Счётчик загрузки"), ("exit", "Выезд")]

    name = models.CharField(max_length=120)
    camera_id = models.CharField(max_length=60, unique=True)
    kind = models.CharField(max_length=20, choices=KINDS)
    api_key = models.CharField(max_length=80)
    response_template = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    last_seen = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def generate_key() -> str:
        return secrets.token_urlsafe(24)

    def __str__(self):
        return f"{self.camera_id} ({self.kind})"


class WebhookCall(models.Model):
    camera = models.ForeignKey(Camera, on_delete=models.CASCADE, related_name="calls")
    plate = models.CharField(max_length=30, blank=True, default="")
    payload_bags = models.IntegerField(null=True, blank=True)
    payload_weight = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    matched_order = models.ForeignKey(
        "orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="webhook_calls"
    )
    decision = models.CharField(max_length=10)
    reason = models.CharField(max_length=300, blank=True, default="")
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
