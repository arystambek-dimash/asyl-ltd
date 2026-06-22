import secrets
from django.db import models


class Camera(models.Model):
    KINDS = [("entry", "Въезд"), ("counter", "Счётчик загрузки"), ("exit", "Выезд")]

    STATUSES = [("pending", "Обнаружена"), ("active", "Активна")]

    name = models.CharField(max_length=120, blank=True, default="")
    camera_id = models.CharField(max_length=60, unique=True)
    kind = models.CharField(max_length=20, choices=KINDS, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUSES, default="active")
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


def video_upload_path(instance, filename):
    return f"videos/order_{instance.order_id}/{filename}"


class VideoJob(models.Model):
    STATUSES = [("queued", "В очереди"), ("processing", "Обработка"),
                ("done", "Готово"), ("failed", "Ошибка")]

    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="video_jobs")
    camera = models.ForeignKey(Camera, on_delete=models.SET_NULL, null=True, related_name="video_jobs")
    video = models.FileField(upload_to=video_upload_path)
    status = models.CharField(max_length=12, default="queued")
    bags_counted = models.PositiveIntegerField(default=0)
    counts_by_class = models.JSONField(default=dict, blank=True)  # {"Red_50": 12, ...}
    error = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
