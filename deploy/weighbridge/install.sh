#!/bin/sh
# Run AFTER this compatible application release has passed all health gates.
# Re-running never upgrades/recreates a running collector.
set -eu
cd "${APP_DIR:-/home/ubuntu/asyl-ltd}"
install_dir="$PWD/.deploy-state/weighbridge"
collector_id="$(docker ps -q --filter label=com.docker.compose.project=asyl-weighbridge --filter label=com.docker.compose.service=collector)"
if [ -n "$collector_id" ] && [ "${1:-}" != "upgrade" ]; then
  docker exec "$collector_id" python -m weighbridge.healthcheck
else
  recreate_args=""
  if [ -n "$collector_id" ]; then
    # An explicit collector upgrade is separate from application deployments.
    # Retain every event and defer while a truck/evidence delivery is active.
    docker exec -i "$collector_id" python - <<'PY'
import time
from weighbridge.outbox import Outbox
box = Outbox('/var/lib/weighbridge')
heartbeat = box.state('heartbeat') or {}
assert time.time() - heartbeat.get('updated_at', 0) <= 2, 'Collector heartbeat is stale'
assert heartbeat.get('clear') and heartbeat.get('armed'), 'Scale is occupied: upgrade deferred'
assert box.counts()['pending'] == 0, 'Evidence delivery is pending: upgrade deferred'
PY
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
  docker compose --env-file "$PWD/.env" -f "$install_dir/compose.yml" up -d --wait --wait-timeout 60 $recreate_args
fi
if [ "${1:-}" = "prepare" ]; then
  exit 0
fi
# The command refuses cutover during a truck/capture. It is safe to retry.
docker compose -f docker-compose.prod.yml exec -T passage-scale-monitor python manage.py activate_weighbridge_collector
