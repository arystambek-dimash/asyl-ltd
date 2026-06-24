from .models import Notification


def notify(client, text: str) -> Notification:
    return Notification.objects.create(client=client, text=text)
