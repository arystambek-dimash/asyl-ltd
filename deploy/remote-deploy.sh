#!/usr/bin/env sh
set -eu

APP_DIR="${APP_DIR:-/home/ubuntu/asyl-ltd}"
BRANCH="${BRANCH:-main}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
EXPECTED_SHA="${EXPECTED_SHA:-}"

# Production must run the exact manifests built by this CI run. A mutable tag
# such as `latest` can change between pull and restart (or be overwritten in the
# registry), so fail closed unless both references are the expected GHCR image
# names followed by a 64-character hexadecimal sha256 digest.
if ! printf '%s\n' "${BACKEND_IMAGE_REF:-}" \
  | grep -Eq '^ghcr\.io/arystambek-dimash/asyl-ltd-backend@sha256:[0-9a-f]{64}$'; then
  echo "BACKEND_IMAGE_REF must be the immutable asyl-ltd backend digest." >&2
  exit 1
fi
if ! printf '%s\n' "${FRONTEND_IMAGE_REF:-}" \
  | grep -Eq '^ghcr\.io/arystambek-dimash/asyl-ltd-frontend@sha256:[0-9a-f]{64}$'; then
  echo "FRONTEND_IMAGE_REF must be the immutable asyl-ltd frontend digest." >&2
  exit 1
fi

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
if [ -n "$EXPECTED_SHA" ]; then
  if ! printf '%s\n' "$EXPECTED_SHA" | grep -Eq '^[0-9a-f]{40}$'; then
    echo "EXPECTED_SHA must be a 40-character Git commit." >&2
    exit 1
  fi
  actual_sha="$(git rev-parse HEAD)"
  if [ "$actual_sha" != "$EXPECTED_SHA" ]; then
    echo "Refusing to deploy unverified commit $actual_sha; expected $EXPECTED_SHA." >&2
    exit 1
  fi
fi

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

# Releases before the non-root backend transition created these persistent
# volumes as root. Image-level chown cannot change an already-mounted volume,
# so repair it in a disposable privileged container before Django starts.
echo "Preparing backend volume permissions..."
docker compose -f "$COMPOSE_FILE" run --rm --no-deps \
  --user root \
  --entrypoint /bin/sh \
  backend -c 'chown -R app:app /app/media /app/staticfiles'

echo "Starting containers..."
if docker compose -f "$COMPOSE_FILE" up -d --remove-orphans --wait --wait-timeout 180; then
  :
else
  status=$?
  echo "Container startup failed. Current state:" >&2
  docker compose -f "$COMPOSE_FILE" ps --all >&2 || true
  echo "backend logs:" >&2
  docker compose -f "$COMPOSE_FILE" logs --no-color --tail=200 backend >&2 || true
  exit "$status"
fi

echo "Restarting go2rtc to pick up bind-mounted config..."
docker compose -f "$COMPOSE_FILE" restart go2rtc

echo "Validating and reloading nginx config (bind-mounted, not picked up by compose)..."
docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -t
docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -s reload

echo "Current containers:"
docker compose -f "$COMPOSE_FILE" ps

# CI builds and caches images remotely. Keep the production host lean after a
# successful, healthy restart without touching running images or data volumes.
echo "Cleaning safe Docker artifacts..."
./deploy/maintenance/cleanup-docker.sh
