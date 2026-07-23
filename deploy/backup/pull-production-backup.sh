#!/usr/bin/env bash
set -euo pipefail

umask 077

production_host="${PRODUCTION_HOST:-ubuntu@78.40.109.240}"
remote_root="${REMOTE_APP_DIR:-/home/ubuntu/asyl-ltd}"
backup_root="${BACKUP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/backups}"
stamp="$(date '+%Y%m%d-%H%M%S-%Z')"
target_dir="${backup_root}/production-${stamp}"
target_file="${target_dir}/asyl-production.dump"
temporary_file="${target_file}.part"
media_target_file="${target_dir}/media-production.tar.gz"
media_temporary_file="${media_target_file}.part"

mkdir -p "$target_dir"
trap 'rm -f "$temporary_file" "$media_temporary_file"' EXIT

echo "Creating and validating a fresh production backup..."
ssh "$production_host" \
  "cd '$remote_root' \
   && docker compose -f docker-compose.prod.yml exec -T db-backup /backup/backup.sh \
   && docker compose -f docker-compose.prod.yml exec -T db-backup \
      pg_restore --list /backups/asyl-latest.dump >/dev/null"

remote_checksum="$(
  ssh "$production_host" \
    "cd '$remote_root' && sha256sum backups/asyl-latest.dump" |
    awk '{print $1}'
)"
media_remote_checksum="$(
  ssh "$production_host" \
    "cd '$remote_root' && sha256sum backups/media-latest.tar.gz" |
    awk '{print $1}'
)"
for checksum in "$remote_checksum" "$media_remote_checksum"; do
  case "$checksum" in
    ""|*[!0-9a-f]*)
      echo "Production did not return a valid SHA-256 checksum." >&2
      exit 1
      ;;
  esac
  if [ "${#checksum}" -ne 64 ]; then
    echo "Production returned an incomplete SHA-256 checksum." >&2
    exit 1
  fi
done

scp \
  "${production_host}:${remote_root}/backups/asyl-latest.dump" \
  "$temporary_file"
scp \
  "${production_host}:${remote_root}/backups/media-latest.tar.gz" \
  "$media_temporary_file"

local_checksum="$(shasum -a 256 "$temporary_file" | awk '{print $1}')"
media_local_checksum="$(
  shasum -a 256 "$media_temporary_file" | awk '{print $1}'
)"
if [ "$local_checksum" != "$remote_checksum" ]; then
  echo "Downloaded backup checksum does not match production." >&2
  exit 1
fi
if [ "$media_local_checksum" != "$media_remote_checksum" ]; then
  echo "Downloaded media checksum does not match production." >&2
  exit 1
fi

mv "$temporary_file" "$target_file"
mv "$media_temporary_file" "$media_target_file"
printf '%s  %s\n' "$local_checksum" "$(basename "$target_file")" \
  >"${target_file}.sha256"
printf '%s  %s\n' "$media_local_checksum" "$(basename "$media_target_file")" \
  >"${media_target_file}.sha256"
chmod 600 \
  "$target_file" "${target_file}.sha256" \
  "$media_target_file" "${media_target_file}.sha256"

echo "Local production backup: $target_file"
echo "SHA-256: $local_checksum"
echo "Local production media: $media_target_file"
echo "SHA-256: $media_local_checksum"
