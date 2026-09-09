import os
import time
from .outbox import Outbox

box = Outbox(os.environ.get("WEIGHBRIDGE_OUTBOX_DIR", "/var/lib/weighbridge"))
heartbeat = box.state("heartbeat") or {}
raise SystemExit(0 if time.time() - heartbeat.get("updated_at", 0) < 15 else 1)
