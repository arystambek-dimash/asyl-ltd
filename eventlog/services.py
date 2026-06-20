from .models import EventLog


def log_event(event_type, message, *, user=None, order=None, payload=None):
    return EventLog.objects.create(
        event_type=event_type, message=message, user=user,
        order=order, payload=payload or {},
    )
