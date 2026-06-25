#!/usr/bin/env sh
set -eu

APP_DIR="${APP_DIR:-/home/ubuntu/asyl-ltd}"
BRANCH="${BRANCH:-main}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

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
docker compose -f "$COMPOSE_FILE" config >/tmp/asyl-ltd-compose-config.yml

echo "Rebuilding and starting containers..."
docker compose -f "$COMPOSE_FILE" up -d --build --remove-orphans

echo "Current containers:"
docker compose -f "$COMPOSE_FILE" ps
