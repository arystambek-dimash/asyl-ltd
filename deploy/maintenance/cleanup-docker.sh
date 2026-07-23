#!/usr/bin/env sh
set -eu

# Production pulls immutable images built by CI and never builds locally.
# Therefore old BuildKit cache has no rollback value. Unused images newer than
# IMAGE_RETENTION are retained for a short emergency rollback window.
image_retention="${IMAGE_RETENTION:-24h}"

echo "Docker disk usage before cleanup:"
docker system df

echo "Removing unused BuildKit cache..."
docker builder prune --all --force

echo "Removing unused images older than ${image_retention}..."
docker image prune --all --force --filter "until=${image_retention}"

echo "Removing stopped one-off containers..."
docker container prune --force --filter "until=24h"

echo "Docker disk usage after cleanup:"
docker system df
