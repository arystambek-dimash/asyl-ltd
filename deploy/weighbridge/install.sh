#!/bin/sh
# Run AFTER this compatible application release has passed all health gates.
# Re-running never upgrades/recreates a running collector.
set -eu
cd "${APP_DIR:-/home/ubuntu/asyl-ltd}"
install_dir="$PWD/.deploy-state/weighbridge"
collector_id="$(docker ps -q --filter label=com.docker.compose.project=asyl-weighbridge --filter label=com.docker.compose.service=collector)"

assert_clear_for_upgrade() {
  docker exec -i "$collector_id" python - <<'PY'
import time
from weighbridge.outbox import Outbox
box = Outbox('/var/lib/weighbridge')
heartbeat = box.state('heartbeat') or {}
assert time.time() - heartbeat.get('updated_at', 0) <= 2, 'Collector heartbeat is stale'
assert heartbeat.get('clear') and heartbeat.get('armed'), 'Scale is occupied: upgrade deferred'
assert not heartbeat.get('pending_writes', 0), 'Collector storage writes are pending: upgrade deferred'
assert box.counts()['pending'] == 0, 'Evidence delivery is pending: upgrade deferred'
PY
}

if [ -n "$collector_id" ] && [ "${1:-}" != "upgrade" ]; then
  docker exec "$collector_id" python -m weighbridge.healthcheck
else
  recreate_args=""
  if [ -n "$collector_id" ]; then
    # An explicit collector upgrade is separate from application deployments.
    # Retain every event and defer while a truck/evidence delivery is active.
    assert_clear_for_upgrade
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
  # Persist the pinned reference; subsequent application image updates do not change it.
  printf '%s\n' "$WEIGHBRIDGE_IMAGE_REF" > "$install_dir/image-ref"
  docker compose -f docker-compose.prod.yml run --rm --no-deps --user root --entrypoint sh passage-scale-monitor -c 'chown app:app /var/lib/weighbridge'
  # Preparing the pinned image/volume can take time. A truck may have arrived
  # since the first guard, so check again immediately before replacement.
  if [ -n "$recreate_args" ]; then
    assert_clear_for_upgrade
  fi
  docker compose --env-file "$PWD/.env" -f "$install_dir/compose.yml" up -d --wait --wait-timeout 60 $recreate_args
fi
if [ "${1:-}" = "upgrade" ]; then
  collector_id="$(docker ps -q --filter label=com.docker.compose.project=asyl-weighbridge --filter label=com.docker.compose.service=collector)"
  # Scale-loop health does not prove that a restarted RTSP relay has decoded
  # its first frame. Verify actual JPEG delivery without printing image data.
  docker exec -i "$collector_id" python - <<'PY'
import http.client
import signal
import time

def expired(*_):
    raise SystemExit('Weighbridge video unavailable: collector upgrade is degraded')

signal.signal(signal.SIGALRM, expired)
signal.alarm(15)
try:
    for attempt in range(3):
        connection = http.client.HTTPConnection('video', 1984, timeout=4)
        try:
            connection.request('GET', '/api/frame.jpeg?src=cam1main')
            response = connection.getresponse()
            frame = response.read(4 * 1024 * 1024 + 1)
            if response.status == 200 and len(frame) <= 4 * 1024 * 1024 and frame.startswith(b'\xff\xd8\xff'):
                print('Weighbridge video frame verified.')
                break
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
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
