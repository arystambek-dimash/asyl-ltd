# Independent weighbridge collector

The `asyl-weighbridge` Compose project owns physical polling and a dedicated
cam1 video relay. Its pinned image and config live in `.deploy-state/weighbridge`,
outside the application checkout. Normal deployments must not stop, recreate,
remove or prune this project or its `asyl-weighbridge-outbox` volume.

The deployment prepares the collector in shadow mode, then activates it after
the old poller stops and before migrations/container startup. If the scale is
busy, activation refuses and the previous writers resume. Subsequent releases
find the durable marker and do not interrupt physical collection.
To retry installation independently, run `sh deploy/weighbridge/install.sh`.
Activation requires three fresh empty readings and no unfinished DB capture. If
busy, retry when the scale clears. An existing collector is never recreated by
this script. The monitor switches to importing the outbox once its durable
`enabled` marker exists. A rollback predating this importer disables its legacy
automatic poller; the collector retains events until a compatible importer is
restored. Do not re-enable the old poller alongside an active collector.

Each stable occupancy keeps its original weight/time and UUID in a FIFO writer
queue. The writer commits the weight to SQLite (`WAL`, `synchronous=FULL`) before
its photo bytes and OCR result. A short SQLite lock delays this write, never
replaces the sample with a later reading. Camera requests start immediately for
the current occupancy while the writer retries independently. Uncommitted data
is buffered in process memory, so power/process loss during a storage outage can
still lose that buffer; a successful SQLite commit is the durability boundary.
Independent bounded evidence workers never block the poller or queue
a future truck's live snapshot. The importer commits PostgreSQL before marking
the UUID acknowledged. A crash between those writes replays idempotently.
Acknowledged data is retained for audit; monitor disk usage and archive offline
before any deliberate retention policy. Do not delete pending records.

SQLite contains sensitive operational data. It has no HTTP endpoint or published
port; restrict Docker/host access. Backup it with SQLite's online backup API,
not by copying a live database file without its WAL.

Health: `docker exec <collector> python -m weighbridge.healthcheck` checks loop
liveness. The private SQLite `state` table holds hardware status, heartbeat and
queue counts; `incidents` records observation gaps/restarts. Liveness does not
mean the scale/camera is reachable. Host failure, power loss, inaccessible
hardware or vehicles that never hold a stable weight cannot be repaired by OCR.
These are visible observation gaps, not fabricated weighings. For protection
against loss of the server itself, an additional collector at the weighbridge PC
and independently powered storage are needed.

For collector upgrades, use a planned empty-scale handoff, retain the volume,
and test replay before changing its pinned image. Application CI/CD should only
verify its health, never upgrade it implicitly.
After the new application image passes production health checks, explicitly run
`sh deploy/weighbridge/install.sh upgrade` (or select `upgrade` in its manual
workflow). It refuses a stale/occupied scale or undelivered events. The queue
volume survives both container replacements. The video relay preloads only the
weighbridge video track so a capture does not need to start a cold RTSP session.
Short scale API errors restart stability confirmation; only a gap exceeding
five seconds discards the previously confirmed occupancy state.
