#!/usr/bin/env sh
set -eu

backup_file="${BACKUP_FILE:-/backups/asyl-latest.dump}"
tmp_file="${backup_file}.tmp"

mkdir -p "$(dirname "$backup_file")"
rm -f "$tmp_file"

pg_dump \
  -h "${POSTGRES_HOST:-db}" \
  -p "${POSTGRES_PORT:-5432}" \
  -U "${POSTGRES_USER:-asyl}" \
  -d "${POSTGRES_DB:-asyl}" \
  -Fc \
  -f "$tmp_file"

# Прошлый дамп сохраняем как .prev: повторный запуск (ретрай деплоя после
# миграций) не должен затирать единственный снапшот до-миграционного состояния.
if [ -f "$backup_file" ]; then
  cp -p "$backup_file" "${backup_file}.prev"
fi
mv "$tmp_file" "$backup_file"
echo "Backup written to $backup_file"
