from django.db import models


class Notification(models.Model):
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="notifications"
    )
    text = models.CharField(max_length=500)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Список клиента: «непрочитанные сверху» — самый частый запрос.
            models.Index(fields=["client", "is_read", "-created_at"],
                         name="notification_inbox_idx"),
        ]
