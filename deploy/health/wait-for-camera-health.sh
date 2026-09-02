#!/usr/bin/env sh
set -eu

# Production-only gate for the complete camera path.  It intentionally runs
# inside Docker over SSH: no monitoring endpoint or credential is exposed to
# the public Internet.
APP_DIR="${APP_DIR:-/home/ubuntu/asyl-ltd}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
GO2RTC_WAIT_SECONDS="${GO2RTC_WAIT_SECONDS:-120}"
CAMERA_HEALTH_WAIT_SECONDS="${CAMERA_HEALTH_WAIT_SECONDS:-210}"
CAMERA_HEALTH_POLL_SECONDS="${CAMERA_HEALTH_POLL_SECONDS:-10}"
CAMERA_HEALTH_MAX_AGE="${CAMERA_HEALTH_MAX_AGE:-180}"
CAMERA_HEALTH_REQUIRE_SINCE_EPOCH="${CAMERA_HEALTH_REQUIRE_SINCE_EPOCH:-}"
CAMERA_HEALTH_REQUIRE_EVENTS="${CAMERA_HEALTH_REQUIRE_EVENTS:-0}"

case "$GO2RTC_WAIT_SECONDS:$CAMERA_HEALTH_WAIT_SECONDS:$CAMERA_HEALTH_POLL_SECONDS:$CAMERA_HEALTH_MAX_AGE" in
  *[!0-9:]* | *::* | :* | *:)
    echo "Camera health timeouts must be positive integer seconds." >&2
    exit 64
    ;;
esac

if [ "$GO2RTC_WAIT_SECONDS" -eq 0 ] || \
   [ "$CAMERA_HEALTH_WAIT_SECONDS" -eq 0 ] || \
   [ "$CAMERA_HEALTH_POLL_SECONDS" -eq 0 ] || \
   [ "$CAMERA_HEALTH_MAX_AGE" -eq 0 ]; then
  echo "Camera health timeouts must be greater than zero." >&2
  exit 64
fi

case "$CAMERA_HEALTH_REQUIRE_SINCE_EPOCH" in
  "") ;;
  *[!0-9]*)
    echo "CAMERA_HEALTH_REQUIRE_SINCE_EPOCH must be a positive Unix timestamp." >&2
    exit 64
    ;;
  *)
    if [ "$CAMERA_HEALTH_REQUIRE_SINCE_EPOCH" -eq 0 ]; then
      echo "CAMERA_HEALTH_REQUIRE_SINCE_EPOCH must be a positive Unix timestamp." >&2
      exit 64
    fi
    ;;
esac

case "$CAMERA_HEALTH_REQUIRE_EVENTS" in
  0 | 1) ;;
  *)
    echo "CAMERA_HEALTH_REQUIRE_EVENTS must be 0 or 1." >&2
    exit 64
    ;;
esac

cd "$APP_DIR"

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

wait_for_go2rtc() {
  attempts=$(( (GO2RTC_WAIT_SECONDS + CAMERA_HEALTH_POLL_SECONDS - 1) / CAMERA_HEALTH_POLL_SECONDS ))
  attempt=1

  while [ "$attempt" -le "$attempts" ]; do
    container_id="$(compose ps -q go2rtc 2>/dev/null || true)"
    health=""
    running="false"
    if [ -n "$container_id" ]; then
      health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container_id" 2>/dev/null || true)"
      running="$(docker inspect --format '{{.State.Running}}' "$container_id" 2>/dev/null || true)"
    fi

    if [ "$running" = "true" ] && [ "$health" = "healthy" ]; then
      echo "go2rtc local API is healthy."
      return 0
    fi

    echo "Waiting for go2rtc local API (running=${running:-unknown}, health=${health:-missing}, attempt $attempt/$attempts)..."
    if [ "$attempt" -lt "$attempts" ]; then
      sleep "$CAMERA_HEALTH_POLL_SECONDS"
    fi
    attempt=$((attempt + 1))
  done

  echo "go2rtc local API did not become healthy within ${GO2RTC_WAIT_SECONDS}s." >&2
  compose ps go2rtc >&2 || true
  return 1
}

wait_for_monitor() {
  attempts=$(( (CAMERA_HEALTH_WAIT_SECONDS + CAMERA_HEALTH_POLL_SECONDS - 1) / CAMERA_HEALTH_POLL_SECONDS ))
  attempt=1
  last_rc=2
  last_output=""

  set -- python manage.py check_camera_health --human --max-age "$CAMERA_HEALTH_MAX_AGE"
  if [ -n "$CAMERA_HEALTH_REQUIRE_SINCE_EPOCH" ]; then
    set -- "$@" --require-since-epoch "$CAMERA_HEALTH_REQUIRE_SINCE_EPOCH"
  fi
  if [ "$CAMERA_HEALTH_REQUIRE_EVENTS" -eq 1 ]; then
    set -- "$@" --require-events
  fi

  while [ "$attempt" -le "$attempts" ]; do
    set +e
    output="$(compose exec -T camera-monitor "$@" 2>&1)"
    rc=$?
    set -e
    last_rc=$rc
    last_output="$output"

    case "$rc" in
      0)
        [ -z "$output" ] || printf '%s\n' "$output"
        echo "Camera monitor has a fresh acceptable heartbeat."
        return 0
        ;;
      2)
        # Bootstrap, unavailable monitor, or stale heartbeat: retry until the
        # bounded deadline so the first deployment can create its snapshot.
        echo "Camera monitor is unavailable or stale (attempt $attempt/$attempts)."
        ;;
      3)
        [ -z "$output" ] || printf '%s\n' "$output" >&2
        echo "Camera health gate failed: confirmed outage or critical video capacity loss." >&2
        return 3
        ;;
      *)
        # Container start/recreate races also land here. Retry, but never turn
        # an unknown check failure into a successful deployment.
        echo "Camera health command failed with exit $rc (attempt $attempt/$attempts)." >&2
        ;;
    esac

    if [ "$attempt" -lt "$attempts" ]; then
      sleep "$CAMERA_HEALTH_POLL_SECONDS"
    fi
    attempt=$((attempt + 1))
  done

  echo "Camera monitor did not produce a fresh heartbeat within ${CAMERA_HEALTH_WAIT_SECONDS}s (last exit ${last_rc})." >&2
  if [ -n "$last_output" ]; then
    echo "Последняя диагностика camera-monitor:" >&2
    printf '%s\n' "$last_output" >&2
  fi
  compose ps camera-monitor go2rtc >&2 || true
  return 2
}

wait_for_go2rtc
wait_for_monitor
