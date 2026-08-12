from .models import EventLog


def log_event(event_type, message, *, user=None, order=None, payload=None):
    event_payload = dict(payload or {})
    if order is not None:
        # EventLog.order uses SET_NULL so audit rows survive hard deletion.
        # Keep the client marker too: department-scoped readers can then keep
        # seeing their own audit trail without orphaned rows becoming global.
        event_payload["client_id"] = order.client_id
    return EventLog.objects.create(
        event_type=event_type, message=message, user=user,
        order=order, payload=event_payload,
    )
