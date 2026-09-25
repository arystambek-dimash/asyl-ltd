"""Explicit accounting destinations for one validated camera event."""

from dataclasses import dataclass

from .event_protocol import CountEvent, EventSyncError, applies_to_continuous_analytics
from .models import ANALYTICS_SCOPE_AI247


@dataclass(frozen=True)
class EventDisposition:
    analytics: bool
    production: bool


def decide_event(event: CountEvent, *, role: str) -> EventDisposition:
    if event.analytics_scope != role:
        raise EventSyncError("AI /events: event analytics scope violates camera role")
    # Old AI-scoped session rows are retained for journal continuity but are
    # shipment crossings, not warehouse production.
    legacy_ai_session = role == ANALYTICS_SCOPE_AI247 and event.mode == "session"
    analytics = applies_to_continuous_analytics(event) and not legacy_ai_session
    return EventDisposition(
        analytics=analytics,
        production=analytics and event.analytics_scope == ANALYTICS_SCOPE_AI247,
    )
