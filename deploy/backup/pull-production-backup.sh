#!/usr/bin/env bash
set -euo pipefail

umask 077

production_host="${PRODUCTION_HOST:-ubuntu@78.40.109.240}"
remote_root="${REMOTE_APP_DIR:-/home/ubuntu/asyl-ltd}"
backup_root="${BACKUP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/backups}"
stamp="$(date '+%Y%m%d-%H%M%S-%Z')"
target_dir="${backup_root}/production-${stamp}"

mkdir -p "$target_dir"
trap 'rm -f "$target_dir"/*.part' EXIT

db_backup() {
  ssh "$production_host" \
    "cd '$remote_root' && docker compose -f docker-compose.prod.yml exec -T db-backup $*"
}

# backup.sh сам проверяет архивы (pg_restore --list, tar -tzf) и пишет их
# .sha256 — забираем эти суммы тем же соединением, что и запускает бэкап.
echo "Creating and validating a fresh production backup..."
remote_checksums="$(
  db_backup "sh -c '/backup/backup.sh >&2 \
    && cat /backups/asyl-latest.dump.sha256 /backups/media-latest.tar.gz.sha256'"
)"

# pull_artifact <имя в /backups> <локальное имя>: скачивает архив, сверяет его
# с суммой, записанной backup.sh, и кладёт рядом локальный .sha256.
pull_artifact() {
  local remote_name="$1"
  local local_file="${target_dir}/$2"
  local remote_checksum local_checksum

  remote_checksum="$(
    awk -v name="/backups/${remote_name}" '$2 == name {print $1}' <<<"$remote_checksums"
  )"
  if ! [[ "$remote_checksum" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Production did not return a valid SHA-256 checksum for ${remote_name}." >&2
    exit 1
  fi

  db_backup "cat /backups/${remote_name}" >"${local_file}.part"
  local_checksum="$(shasum -a 256 "${local_file}.part" | awk '{print $1}')"
  if [ "$local_checksum" != "$remote_checksum" ]; then
    echo "Downloaded ${remote_name} checksum does not match production." >&2
    exit 1
  fi

  mv "${local_file}.part" "$local_file"
  printf '%s  %s\n' "$local_checksum" "$(basename "$local_file")" >"${local_file}.sha256"
  chmod 600 "$local_file" "${local_file}.sha256"
  echo "Local production ${remote_name}: $local_file"
  echo "SHA-256: $local_checksum"
}

pull_artifact asyl-latest.dump asyl-production.dump
pull_artifact media-latest.tar.gz media-production.tar.gz
