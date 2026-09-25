# Shipping session monitor

`shipping-transport-monitor` uses the backend image and runs
`python manage.py monitor_shipping_sessions` continuously (since 2026-09-17).
It groups the durable bag counts of each shipping conveyor into numbered
loading sessions and idle segments, saves one first frame per segment and reads
its number. It never acquires orders or changes order statuses: the monoblock
is view-only, and shipping is registered by the loader on the «Грузчик» page,
which also closes the order's open AI count. Session rules and API:
`deploy/shipping-sessions.md`.

Three bounded pools (count import, first-frame capture, number reading) run
independently, four workers each: slow OCR/GPT does not delay count import or
photographs of other conveyors. With no shipping cameras or an unconfigured AI
service, polling is harmless. An open browser or monoblock page is not required.

Number-camera settings include an optional normalized rectangular loading
zone (`loading_zone: [x1,y1,x2,y2]`, null means whole frame). Configure it in
the monoblock's «Камера номера» tab while the conveyor has no active shipment.
A new segment fixes the zone; the number is read from that crop, and the card
keeps the full frame.

The worker and backend share the writable `mediadata:/app/media` volume: the
worker saves the photo, and the backend serves it through the protected image
API. Both containers must retain this same volume across restarts and releases.

Production Compose starts one worker after the backend has completed migrations
and passed its health check, and after go2rtc is healthy. The development worker
belongs to the `hardware` profile, so an ordinary local Compose start does not
read physical cameras.

Runtime configuration:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SHIPPING_TRANSPORT_POLL_SECONDS` | `2` | Target seconds between count imports for each conveyor; allowed range 0.5–60. |
| `SHIPPING_TRANSPORT_HEARTBEAT_FILE` | `/tmp/shipping-transport-monitor/heartbeat.json` | Container-private, atomically replaced process heartbeat. |
| `SHIPPING_TRANSPORT_HEARTBEAT_MAX_AGE_SECONDS` | `180` | Maximum age of the supervisor loop heartbeat. |

Dependency failures are retried; camera errors publish a `degraded` heartbeat
that still counts as a live process. Successful iterations publish `running`.
A missing, invalid or stale heartbeat fails the health check. Process liveness
alone does not prove that a camera has counted bags or that a number was read.

To inspect the running worker:

```sh
docker compose -f docker-compose.prod.yml ps shipping-transport-monitor
docker compose -f docker-compose.prod.yml logs --tail=100 shipping-transport-monitor
docker compose -f docker-compose.prod.yml exec -T shipping-transport-monitor \
  python /app/shipping_transport_monitor_healthcheck.py
```

`--once` runs one bounded diagnostic pass and exits, propagating dependency
failures to its caller. It writes sessions, segments and photos like the
continuous worker; use it with isolated test data and mocked cameras.

SIGTERM and SIGINT stop new work and allow active bounded requests to finish.
Compose allows 180 seconds for shutdown. The deployment script stops this
worker with the other camera writers before candidate migrations; a candidate
that fails to start resumes the previous writers. For manual database
maintenance, stop this worker along with all application writers before
changing the schema or restoring data.
