# Production backups

The production database is dumped every day at 03:00 Asia/Almaty and before
each deploy. Dumps are written atomically and validated with `pg_restore`
before the previous good copy is rotated.

Server retention is intentionally small:

- `backups/asyl-latest.dump` — latest validated dump;
- `backups/asyl-latest.dump.prev` — one rollback copy.
- `backups/media-latest.tar.gz` — latest validated uploaded-file archive;
- `backups/media-latest.tar.gz.prev` — one media rollback copy.

Data volumes are never touched by the Docker cleanup task.

## Pull a fresh local copy

From the repository root:

```bash
./deploy/backup/pull-production-backup.sh
```

The script creates a timestamped directory under the gitignored `backups/`
folder, downloads both PostgreSQL and media, compares production and local
SHA-256 checksums, and restricts file permissions to the current user.

Optional overrides:

```bash
PRODUCTION_HOST=user@host \
REMOTE_APP_DIR=/path/to/app \
BACKUP_ROOT=/secure/local/path \
./deploy/backup/pull-production-backup.sh
```

## Restore drill

Always restore into a new empty database first. Never test a dump by restoring
over production.

```bash
createdb asyl_restore_check
pg_restore --clean --if-exists --no-owner \
  --dbname asyl_restore_check \
  backups/production-YYYYMMDD-HHMMSS-TZ/asyl-production.dump
```

If the local PostgreSQL client is older than PostgreSQL 16, run the restore
with a `postgres:16-alpine` container or install a PostgreSQL 16 client.
