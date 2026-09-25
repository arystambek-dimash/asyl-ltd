#!/bin/sh
# Run AFTER this compatible application release has passed all health gates.
# Re-running never upgrades/recreates a running collector.
set -eu
cd "${APP_DIR:-/home/ubuntu/asyl-ltd}"
install_dir="$PWD/.deploy-state/weighbridge"
collector_id="$(docker ps -q --filter label=com.docker.compose.project=asyl-weighbridge --filter label=com.docker.compose.service=collector)"
wagon_collector_id="$(docker ps -q --filter label=com.docker.compose.project=asyl-weighbridge --filter label=com.docker.compose.service=wagon-collector)"

assert_clear_for_upgrade() {
  docker exec -i "$collector_id" python - <<'PY'
import time
from weighbridge.outbox import Outbox
box = Outbox('/var/lib/weighbridge')
heartbeat = box.state('heartbeat') or {}
if time.time() - heartbeat.get('updated_at', 0) > 2:
    raise SystemExit('Collector heartbeat is stale')
if not (heartbeat.get('clear') and heartbeat.get('armed')):
    raise SystemExit('Scale is occupied: upgrade deferred')
if heartbeat.get('pending_writes', 0):
    raise SystemExit('Collector storage writes are pending: upgrade deferred')
if box.counts()['pending']:
    raise SystemExit('Evidence delivery is pending: upgrade deferred')
PY
}

assert_wagon_clear_for_upgrade() {
  # Only meaningful once the second service exists; a first install has no
  # wagon collector to defer to.
  if [ -n "$wagon_collector_id" ]; then
    docker exec -i "$wagon_collector_id" python - <<'PY'
import time
from weighbridge.outbox import Outbox
box = Outbox('/var/lib/weighbridge-wagon')
heartbeat = box.state('heartbeat') or {}
if time.time() - heartbeat.get('updated_at', 0) > 2:
    raise SystemExit('Wagon collector heartbeat is stale')
# Replacing the container mid-unloading is safe (the restarted collector
# re-adopts the stop) but the blind interval is recorded as a motion gap,
# so an upgrade waits for the arch to be empty instead.
if heartbeat.get('standing') is not None:
    raise SystemExit('Wagon stands under the arch: upgrade deferred')
if heartbeat.get('pending_writes', 0):
    raise SystemExit('Wagon collector storage writes are pending: upgrade deferred')
# Pending EVENTS are deliberately not checked: wagon rows stay unacknowledged
# until the CRM importer is switched on (WAGON_ARCH_AUTOMATION_ENABLED).
PY
  fi
}

if [ -n "$collector_id" ] && [ "${1:-}" != "upgrade" ]; then
  docker exec "$collector_id" python -m weighbridge.healthcheck
else
  recreate_args=""
  if [ -n "$collector_id" ]; then
    # An explicit collector upgrade is separate from application deployments.
    # Retain every event and defer while a truck/evidence delivery is active.
    assert_clear_for_upgrade
    assert_wagon_clear_for_upgrade
    recreate_args="--force-recreate"
  fi
  mkdir -p "$install_dir"
  chmod 700 "$install_dir"
  cp deploy/weighbridge/compose.yml deploy/weighbridge/go2rtc.yaml "$install_dir/"
  if [ -z "${WEIGHBRIDGE_IMAGE_REF:-}" ]; then
    backend_id="$(docker compose -f docker-compose.prod.yml ps -q backend)"
    WEIGHBRIDGE_IMAGE_REF="$(docker inspect --format '{{.Config.Image}}' "$backend_id")"
  fi
  case "$WEIGHBRIDGE_IMAGE_REF" in *@sha256:*) ;; *) echo 'Immutable image required' >&2; exit 1;; esac
  export WEIGHBRIDGE_IMAGE_REF
  # Manual activation has no deployment-shell image variables. Use the already
  # running, verified backend image for the one-off volume-permission helper.
  export BACKEND_IMAGE_REF="$WEIGHBRIDGE_IMAGE_REF"
  docker compose -f docker-compose.prod.yml run --rm --no-deps --user root --entrypoint sh passage-scale-monitor -c 'chown app:app /var/lib/weighbridge /var/lib/weighbridge-wagon'
  # Preparing the pinned image/volume can take time. A truck may have arrived
  # since the first guard, so check again immediately before replacement.
  if [ -n "$recreate_args" ]; then
    assert_clear_for_upgrade
    assert_wagon_clear_for_upgrade
  fi
  docker compose --env-file "$PWD/.env" -f "$install_dir/compose.yml" up -d --wait --wait-timeout 60 $recreate_args
fi
if [ "${1:-}" = "upgrade" ]; then
  collector_id="$(docker ps -q --filter label=com.docker.compose.project=asyl-weighbridge --filter label=com.docker.compose.service=collector)"
  # Scale-loop health does not prove that a restarted RTSP relay has decoded
  # its first frame. Verify actual JPEG delivery without printing image data.
  docker exec -i "$collector_id" python - <<'PY'
import signal
import time
from django.conf import settings
from weighbridge.runtime import fetch_frame

def expired(*_):
    raise SystemExit('Weighbridge video unavailable: collector upgrade is degraded')

signal.signal(signal.SIGALRM, expired)
signal.alarm(15)
try:
    for attempt in range(3):
        try:
            frame = fetch_frame(settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA)
        except Exception:
            frame = None
        if frame is not None:
            print('Weighbridge video frame verified.')
            break
        if attempt < 2:
            time.sleep(1)
    else:
        expired()
finally:
    signal.alarm(0)
PY
fi
if [ "${1:-}" = "prepare" ]; then
  exit 0
fi
# The command refuses cutover during a truck/capture. It is safe to retry.
docker compose -f docker-compose.prod.yml exec -T passage-scale-monitor python manage.py activate_weighbridge_collector
