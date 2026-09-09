"""Count-driven shipping monitor; does not acquire orders by repeated OCR."""

from . import monitor_shipping_transports as supervisor
from apps.cameras import shipping_session_scheduler


class Command(supervisor.Command):
    help = "Group durable shipping counts into numbered sessions and idle segments"

    def handle(self, *args, **options):
        # Reuse the signal-safe heartbeat supervisor with explicit dependencies;
        # the legacy command remains available for old-session diagnostics.
        return self.run_monitor(
            *args, scheduler_class=shipping_session_scheduler.ShippingSessionScheduler,
            once_callback=shipping_session_scheduler.poll_once, **options,
        )
