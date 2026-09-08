# Automatic shipping transport monitor

`shipping-transport-monitor` uses the backend image and runs
`python manage.py monitor_shipping_transports` continuously. Saving a shipping
conveyor's number-camera binding makes that lane available to the polling
worker. With no bindings or an unconfigured AI service, polling is harmless.
An open browser or monoblock page is not required.

New acquisitions also require a ready whole-transport detector on the camera
PC (`AI_TRANSPORT_MODEL_ENABLED=true`, `AI_TRANSPORT_MODEL_PATH`) and a fresh
stationary body associated with the recognized number. The number-only model
cannot prove presence or departure. Install and validate the separate body
checkpoint before enabling this release's automatic loading. Disabled, missing
or unavailable body tracking leaves presence unknown and prevents new claims;
existing counting sessions are retained. Apply migrations `0033`, `0034` and `0035`.

Automatic loading completion requires both confirmed transport absence and
fresh conveyor evidence continuously for 40 seconds. Conveyor evidence comes
from successful inference frames, all raw bag detections (including weak
candidates), frame quality and generic scene motion. It is not inferred from
an unchanged total. Presence, activity, stale/unusable frames, capture restart
or a monitoring gap reset the countdown. This detects moving non-bag cargo
conservatively; there is no separate pallet classifier, so stationary pallets
are not guaranteed to be recognized. Continuous belt motion or camera shake
can conservatively prevent automatic completion and require scene calibration.

Number-camera settings include an optional normalized rectangular loading
zone (`loading_zone: [x1,y1,x2,y2]`, null means whole frame). Configure it in
the monoblock's “Камера номера” tab while the conveyor has no active order.
Keep the full body of the loading vehicle inside the rectangle. Transport
outside the zone is ignored; an uncertain body crossing the boundary prevents
an empty-zone conclusion. Old CV must explicitly acknowledge a requested zone.

Completion uses the new guarded `POST /processors/{cam}/finish-automatic` API.
The camera PC waits for a newly captured inference frame, processes its count,
checks the idle guard and freezes that exact session's final result. Backend
commits a finishing intent first. Lost responses are resolved using a read-only
receipt replay before any counter restoration; a returned vehicle cannot be
closed by such a recovery request. Old CV cannot silently ignore the guard.
The order becomes `loaded`, its actual bag total is retained, and the 24/7
processor continues counting analytics. `shipped`/exit remains an explicit
operator action. Manual finish remains available for operator intervention.

Recognized numbers, snapshot photos, and observation times are saved as evidence
and shown in the monoblock/order details. If no order matches or several orders
match, evidence is retained for later manual review without choosing an order.
Raw numbers without a confirmed body association are retained as `observed`
evidence and cannot acquire an order.
The worker and backend share the writable `mediadata:/app/media` volume: the
worker saves the photo, and the backend serves it through the protected image
API. Both containers must retain this same volume across restarts and releases.

Production Compose starts one worker after the backend has completed migrations
and passed its health check, and after go2rtc is healthy. The development worker
belongs to the `hardware` profile, so an ordinary local Compose start does not
read physical cameras or acquire orders.

Runtime configuration:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SHIPPING_TRANSPORT_POLL_SECONDS` | `2` | Target seconds between poll starts for each lane; allowed range 0.5–60. |
| `SHIPPING_TRANSPORT_HEARTBEAT_FILE` | `/tmp/shipping-transport-monitor/heartbeat.json` | Container-private, atomically replaced process heartbeat. |
| `SHIPPING_TRANSPORT_HEARTBEAT_MAX_AGE_SECONDS` | `180` | Maximum age of the supervisor loop heartbeat. |

Each lane has at most one active poll. A persistent pool runs up to 32 lanes
independently: a slow camera does not delay the next frame from another lane.
Additional configured lanes wait in due-time order, without an unbounded queue.
Dependency failures are retried;
camera errors publish a `degraded` heartbeat that still counts as a live process.
Successful iterations publish `running`. A missing, invalid or stale heartbeat
fails the health check. Process liveness alone does not prove that a camera has
recognized a number or that a matching order exists. Presence ages to `unknown`
from the camera observation timestamp even while the process heartbeat is fresh.

To inspect the running worker:

```sh
docker compose -f docker-compose.prod.yml ps shipping-transport-monitor
docker compose -f docker-compose.prod.yml logs --tail=100 shipping-transport-monitor
docker compose -f docker-compose.prod.yml exec -T shipping-transport-monitor \
  python /app/shipping_transport_monitor_healthcheck.py
```

`--once` runs one iteration and exits, propagating dependency failures to its
caller. It performs the same order mutations as the continuous worker; use it
with isolated test data and mocked cameras for local verification.

SIGTERM and SIGINT prevent new lane polls and allow active bounded requests
to finish. Compose allows 180 seconds for shutdown. The deployment
script stops this worker with the other camera writers before the cutover check
and candidate migrations; a refused cutover resumes the previous writers.
For manual database maintenance, stop this worker along with all application
writers before changing the schema or restoring data.
