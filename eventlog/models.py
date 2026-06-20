from django.conf import settings
from django.db import models


class EventLog(models.Model):
    event_type = models.CharField(max_length=50)
    message = models.CharField(max_length=500)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    order = models.ForeignKey(
        "orders.Order", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="events",
    )
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("EventLog записи неизменяемы")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("EventLog записи нельзя удалять")
