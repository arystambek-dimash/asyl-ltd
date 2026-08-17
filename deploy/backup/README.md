# Production backups

The production database is dumped every day at 03:00 Asia/Almaty and before
each deploy. Dumps are written atomically and validated with `pg_restore`
before the previous good copy is rotated.

Server retention is intentionally small:

- `backups/asyl-latest.dump` — latest validated dump;
- `backups/asyl-latest.dump.prev` — one rollback copy.
- `backups/media-latest.tar.gz` — latest validated uploaded-file archive;
- `backups/media-latest.tar.gz.prev` — one media rollback copy.

Each archive has a matching `.sha256` manifest. Rotated manifests are
regenerated after the archive becomes `.prev`, so `sha256sum -c` always checks
the intended generation.

Data volumes are never touched by the Docker cleanup task.

Production deploys fail closed if `db-backup` is not running or the
pre-deploy database/media backup cannot be written and validated. On a new,
empty host, configure `.env`, explicitly pull the infrastructure images, then
start `db` and `db-backup` before the normal deploy:

```bash
docker compose -f docker-compose.prod.yml pull \
  db redis go2rtc nginx certbot db-backup wireguard
docker compose -f docker-compose.prod.yml up -d --wait db db-backup
```

There is no unsafe “skip backup” flag. Normal application deploys pull only
the immutable backend/frontend release digests and run with `--pull never`, so
an unrelated mutable infrastructure tag cannot change during a release.

## Automatic release rollback

GitHub repository or organization secrets must provide `PROD_HOST`,
`PROD_SSH_KEY`, `PROD_SSH_KNOWN_HOSTS` and `TRUCK_SCALE_API_URL`. They must be
available to both the `deploy` and unattended `recovery` jobs; do not place
them only in an approval-gated environment that recovery does not bind to.
Populate the host-key secret from a separately verified production fingerprint
(the workflow uses strict host-key checking; it does not trust the first key
seen); for a non-default port, use the standard `[host]:port` known-hosts form.
`PROD_PORT` and `PROD_USER` may be omitted only to use their explicit
`22` and `ubuntu` defaults; the application path is derived as
`/home/<PROD_USER>/asyl-ltd`. Missing required connection settings fail the
workflow before any host mutation. The deploy and recovery jobs use a read-only
GitHub token for repository/package access.

Before changing the checkout or recreating application containers, the deploy
records the running backend/frontend digest references and Git commit in the
gitignored, host-only `.deploy-state/` directory (mode `0700`). That directory
is not mounted into any container; the database backup container can write only
archive data under `backups/`. The same pending state is reused by an SSH retry,
so a partially started candidate can never become its own rollback target.
Local compose, go2rtc, or nginx startup failure restores that recorded release
immediately.

GitHub Actions keeps the transaction pending while it checks the API and login
flow through the public site. Either gate failing invokes the persisted rollback
runner, restores the previous Git checkout (including bind-mounted nginx/go2rtc
configuration), and re-pins both application images. Only after both gates pass
does the workflow mark the candidate good and prune unused Docker images.
If the main deploy job itself times out, an independent recovery job consumes
the same durable transaction: it rolls back a candidate that failed before the
public gates, or finishes finalization when both gates had already passed.

This is an **application release rollback**, not a database restore. The backend
runs Django migrations during container startup, so production migrations must
follow the expand/contract rule: a previous application release must remain
compatible with the migrated schema. Database and media archives are never
restored automatically because doing so could discard writes accepted after the
deploy began. Use the validated pre-deploy archives for a deliberate maintenance
restore if a migration itself must be reversed.

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
