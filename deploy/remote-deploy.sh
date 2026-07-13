#!/usr/bin/env sh
set -eu

APP_DIR="${APP_DIR:-/home/ubuntu/asyl-ltd}"
BRANCH="${BRANCH:-main}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

# Только один деплой одновременно: ретрай из CI не должен гоняться с ещё
# живой первой попыткой (git pull об index.lock, docker compose о контейнеры).
# Ждём завершения предыдущего экземпляра до 15 минут.
LOCK_FILE="${LOCK_FILE:-/tmp/asyl-ltd-deploy.lock}"
exec 9>"$LOCK_FILE"
if ! flock -w 900 9; then
  echo "Не дождались завершения другого деплоя за 15 минут — выходим." >&2
  exit 1
fi

cd "$APP_DIR"

echo "Deploying ${BRANCH} in ${APP_DIR}"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

if docker compose -f "$COMPOSE_FILE" ps --services --filter status=running | grep -qx db-backup; then
  echo "Writing pre-deploy database backup..."
  docker compose -f "$COMPOSE_FILE" exec -T db-backup /backup/backup.sh
fi

echo "Validating compose config..."
# `config` expands all environment values, including camera/alert credentials.
# Quiet validation keeps those secrets out of a world-readable /tmp file.
docker compose -f "$COMPOSE_FILE" config --quiet

# Образы собираются в CI (GitHub Actions) и публикуются в GHCR —
# сервер их только скачивает, ничего не собирая сам.
if [ -n "${GHCR_TOKEN:-}" ]; then
  echo "Logging in to ghcr.io..."
  printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "${GHCR_USER:-github}" --password-stdin
fi

echo "Pulling images..."
docker compose -f "$COMPOSE_FILE" pull --quiet

# The camera gate must observe a probe from this deployment, rather than
# accepting a still-young heartbeat left by the previous monitor container.
# Add one second because the CLI accepts whole Unix seconds while Django stores
# microseconds; this closes the same-second race with the previous heartbeat.
CAMERA_DEPLOY_EPOCH="$(( $(date +%s) + 1 ))"

echo "Starting containers..."
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans

echo "Restarting go2rtc to pick up bind-mounted config..."
docker compose -f "$COMPOSE_FILE" restart go2rtc

echo "Waiting for go2rtc and a fresh camera-monitor heartbeat..."
APP_DIR="$APP_DIR" \
COMPOSE_FILE="$COMPOSE_FILE" \
CAMERA_HEALTH_REQUIRE_SINCE_EPOCH="$CAMERA_DEPLOY_EPOCH" \
  ./deploy/health/wait-for-camera-health.sh

echo "Validating and reloading nginx config (bind-mounted, not picked up by compose)..."
docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -t
docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -s reload

echo "Current containers:"
docker compose -f "$COMPOSE_FILE" ps
