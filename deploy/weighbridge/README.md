# Independent weighbridge collector

The `asyl-weighbridge` Compose project owns physical polling and a dedicated
video relay (cam1 for the truck lane, cam8 for the wagon arch — see «Wagon
collector» below). Its pinned image and config live in
`.deploy-state/weighbridge`, outside the application checkout. Normal
deployments must not stop, recreate, remove or prune this project or its
`asyl-weighbridge-outbox`/`asyl-weighbridge-wagon-outbox` volumes.

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

After a capture the lane waits for the next vehicle. Trucks queue through the
scale, so it may never read empty between them: besides a confirmed clear, the
lane re-arms when the load rises `VEHICLE_PLATE_AUTO_SCALE_REARM_DELTA_KG`
(default 1000) above the captured weight, or first falls that much below it and
then rises that much again. Each condition must hold on two consecutive fresh
readings. A truck that stops half off the scale only falls and is not captured
again. Every such re-arm is recorded as a `rearmed_by_weight_change:<path>`
incident. A scale-link outage (`observation_gap`) disarms the lane but keeps
this watch, so a queue that kept moving during the outage still re-arms it; a
collector start still requires a clear scale. The collector image changes only
through the activation workflow.

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
workflow). It refuses a stale/occupied scale or undelivered events, and — once
the wagon collector is installed — also refuses while a wagon stands under the
arch or its storage writes are pending (undelivered wagon *events* do not
defer it: they stay unacknowledged until the CRM importer is switched on). The queue
volume survives both container replacements. The video relay preloads only the
weighbridge video track so a capture does not need to start a cold RTSP session.
Short scale API errors restart stability confirmation; only a gap exceeding
five seconds discards the previously confirmed occupancy state.

## Wagon collector

`wagon-collector` is a second service in the same `asyl-weighbridge` project.
It watches the wagon scale under the unloading arch instead of the truck lane,
using its own outbox volume (`asyl-weighbridge-wagon-outbox`, mounted at
`/var/lib/weighbridge-wagon`) so a truck-collector upgrade or rollback never
touches it. Unlike the truck collector it has no activation marker: there is
no legacy poller to hand off from, so `sh deploy/weighbridge/install.sh
upgrade` simply brings both services up together and the wagon collector
starts recording immediately. The CRM only imports its events once
`WAGON_ARCH_AUTOMATION_ENABLED=1` is set on the server (default `0`); with the
flag off the collector still records every stop to its own SQLite outbox, the
importer just leaves it alone.

`WAGON_SCALE_API_URL` must be set in the server's `.env`. The wagon indicator
is a CAS Weight API; its current address (2026-09-14) is
`http://vesyv.taild494e4.ts.net:8000/api/v1/weight` over Tailscale. The
`http://vesyv:8000/api/v1/weight` baked into `compose.yml` as a fallback is
only a placeholder — it resolves nowhere outside a network with a `vesyv`
host and exists so the container still starts (and reports
`hardware_unavailable`) when the real address is not yet configured on that
server.

It also needs two things from the camera-PC package (Part 1): a `cam8main`
stream in this relay (already in `go2rtc.yaml`, deliberately not in
`preload` — a wagon frame is only ever fetched around a stop, not streamed
continuously) and `GET /cameras/cam8/arch-motion` answering `{state:
"moving"|"still"|"unknown", still_seconds, sample_age_seconds}`. Until Part 1
is deployed and its motion zone is drawn, the collector's status is
`camera_unavailable` (logged once per status transition, not once per poll)
and no stop is recorded — an `unknown` motion state never closes a stop, so a
missing or misconfigured Part 1 is inert, not unsafe.

Each wagon produces two outbox rows. Arrival (`"kind": "wagon_stop"`) carries
the full weight, a cam8 frame and the OCR'd wagon number, filled in by the
same evidence workers as the truck collector. Departure (`"kind":
"wagon_departure"`) carries the empty weight, `"stop_id"` pointing back at the
arrival's `"id"`, and `"motion_gap": true` when the wagon left while motion
was unreadable. Both rows share the truck collector's `Outbox`/`OutboxWriter`
machinery and the FIFO/durability guarantees described above; the CRM
importer (Part 3) is the consumer of these shapes.

The wagon standing under the arch is persisted (`state` key `standing`), so a
crash, a host reboot or a container replacement mid-unloading re-adopts that
stop instead of recording a second arrival with its mid-unloading weight. The
restart interval was unwatched, so the eventual departure carries
`"motion_gap": true` and an incident `standing_restored:<stop id>` is logged.

Every `WAGON_ARCH_*` setting is passed through `compose.yml` from the server's
`.env` (see `.env.example` for the full list and defaults): the arrival
thresholds (`STILL_SECONDS`, `STABLE_SECONDS`, `STABLE_TOLERANCE_KG`,
`EMPTY_MAX_KG`, `NEXT_WAGON_RISE_KG`), the motion sample lifetime
(`MOTION_MAX_AGE_SECONDS`), the OCR retry budget (`OCR_RETRY_SECONDS`,
`OCR_MAX_ATTEMPTS`), the camera (`CAMERA`) and the CRM import flag
(`AUTOMATION_ENABLED`).

To inspect the queue directly:

```
docker exec asyl-weighbridge-wagon-collector-1 python -c "
import sqlite3
conn = sqlite3.connect('/var/lib/weighbridge-wagon/events.sqlite3')
for row in conn.execute('select id, ready, acknowledged, body from events order by seq desc limit 20'):
    print(row)
"
```
