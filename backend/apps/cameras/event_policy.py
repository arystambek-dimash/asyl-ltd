"""Explicit accounting destinations for one validated camera event."""

from dataclasses import dataclass

from .event_protocol import CountEvent, EventSyncError, _applies_to_continuous_analytics
from .models import ANALYTICS_SCOPE_AI247, ANALYTICS_SCOPE_SHIPPING


@dataclass(frozen=True)
class EventDisposition:
    analytics: bool
    production: bool
    shipping_bootstrap: bool

    @property
    def daily(self) -> bool:
        return self.analytics or self.shipping_bootstrap


def decide_event(
    event: CountEvent,
    *,
    role: str,
    pending_shipping_bootstrap: bool,
) -> EventDisposition:
    bootstrap_tail = (
        role == ANALYTICS_SCOPE_SHIPPING
        and pending_shipping_bootstrap
        and event.analytics_scope == ANALYTICS_SCOPE_AI247
    )
    if event.analytics_scope != role and not bootstrap_tail:
        raise EventSyncError("AI /events: event analytics scope violates camera role")
    # Old AI-scoped session rows are retained for journal continuity but are
    # shipment crossings, not warehouse production. The bootstrap exception
    # copies only the rollback baseline before the additive shipping seed.
    legacy_ai_session = role == ANALYTICS_SCOPE_AI247 and event.mode == "session"
    continuous = _applies_to_continuous_analytics(event) and not legacy_ai_session
    bootstrap = continuous and bootstrap_tail
    analytics = continuous and not bootstrap
    return EventDisposition(
        analytics=analytics,
        production=analytics and event.analytics_scope == ANALYTICS_SCOPE_AI247,
        shipping_bootstrap=bootstrap,
    )
