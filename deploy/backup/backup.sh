#!/usr/bin/env sh
set -eu

umask 077

backup_file="${BACKUP_FILE:-/backups/asyl-latest.dump}"
tmp_file="${backup_file}.tmp"
previous_file="${backup_file}.prev"
lock_dir="${backup_file}.lock"
media_backup_file="${MEDIA_BACKUP_FILE:-/backups/media-latest.tar.gz}"
media_tmp_file="${media_backup_file}.tmp"
media_previous_file="${media_backup_file}.prev"
backup_checksum_tmp="${backup_file}.sha256.tmp"
previous_checksum_tmp="${previous_file}.sha256.tmp"
media_checksum_tmp="${media_backup_file}.sha256.tmp"
media_previous_checksum_tmp="${media_previous_file}.sha256.tmp"

mkdir -p "$(dirname "$backup_file")"

# Cron and a production deploy can request a backup at the same time. An
# atomic directory lock prevents one dump from rotating the other's files.
if ! mkdir "$lock_dir" 2>/dev/null; then
  echo "Backup is already running: $lock_dir" >&2
  exit 75
fi
cleanup() {
  rm -f \
    "$tmp_file" \
    "$media_tmp_file" \
    "$backup_checksum_tmp" \
    "$previous_checksum_tmp" \
    "$media_checksum_tmp" \
    "$media_previous_checksum_tmp"
  rmdir "$lock_dir" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

write_checksum() {
  archive="$1"
  checksum_tmp="$2"
  sha256sum "$archive" >"$checksum_tmp"
  mv -f "$checksum_tmp" "${archive}.sha256"
}

pg_dump \
  -h "${POSTGRES_HOST:-db}" \
  -p "${POSTGRES_PORT:-5432}" \
  -U "${POSTGRES_USER:-asyl}" \
  -d "${POSTGRES_DB:-asyl}" \
  -Fc \
  -f "$tmp_file"

# Never rotate a good backup until PostgreSQL confirms the new custom-format
# archive can be read.
pg_restore --list "$tmp_file" >/dev/null

# User-uploaded files live outside PostgreSQL. Archive the read-only media
# volume as part of the same backup run and validate the gzip stream.
tar -C "${MEDIA_ROOT:-/media}" -czf "$media_tmp_file" .
tar -tzf "$media_tmp_file" >/dev/null

# Прошлый дамп сохраняем как .prev: повторный запуск (ретрай деплоя после
# миграций) не должен затирать единственный снапшот до-миграционного состояния.
if [ -f "$backup_file" ]; then
  mv -f "$backup_file" "$previous_file"
  # Regenerate instead of renaming the latest manifest: sha256sum embeds the
  # archive path, so a renamed manifest would still validate `latest`.
  write_checksum "$previous_file" "$previous_checksum_tmp"
fi
rm -f "${backup_file}.sha256"
if [ -f "$media_backup_file" ]; then
  mv -f "$media_backup_file" "$media_previous_file"
  write_checksum "$media_previous_file" "$media_previous_checksum_tmp"
fi
rm -f "${media_backup_file}.sha256"
mv "$tmp_file" "$backup_file"
mv "$media_tmp_file" "$media_backup_file"
write_checksum "$backup_file" "$backup_checksum_tmp"
write_checksum "$media_backup_file" "$media_checksum_tmp"
echo "Backups written to $backup_file and $media_backup_file"
