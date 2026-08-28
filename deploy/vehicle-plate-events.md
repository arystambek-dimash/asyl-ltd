# Vehicle plate events: production integration

The camera-PC posts metadata for a confirmed stationary vehicle. It never
posts, requests, or stores a photo or a video in Asyl-LTD.

## Production endpoint and authentication

Use this exact HTTPS URL (there is no trailing slash):

```text
https://asyl-ltd.kz/api/integrations/vehicle-plate-events
```

The HTTPS virtual host is the only public route to the endpoint. Port 80
redirects to HTTPS; Django additionally receives `X-Forwarded-Proto: https`
from nginx and rejects a non-secure request.

The camera-PC sends these headers:

```text
Content-Type: application/json
Authorization: Bearer <VEHICLE_PLATE_WEBHOOK_TOKEN>
Idempotency-Key: <event_id>
```

`VEHICLE_PLATE_WEBHOOK_TOKEN` is a separate, high-entropy production secret.
Do not reuse `AI_SERVICE_API_KEY`, do not place it in a URL, an image, Git,
browser variables (`NEXT_PUBLIC_*`), CI output, or application logs.

On the production host, add the value only to the gitignored `.env` file
(mode `0600`), then validate and recreate the backend through the normal
release path. `docker-compose.prod.yml` refuses to start without it. The CI
release workflow deliberately does not transport this secret: Compose reads it
from the protected production host environment and passes it only to backend
containers.

```dotenv
# /home/ubuntu/asyl-ltd/.env — never commit this value
VEHICLE_PLATE_WEBHOOK_TOKEN=<generate-a-new-32+-character-secret>
VEHICLE_PLATE_WEBHOOK_MAX_BODY_BYTES=65536
THROTTLE_VEHICLE_PLATE_WEBHOOK=120/min
```

Generate and deliver the token to the Windows camera-PC through an approved
secret channel. A token rotation starts by provisioning the new value on the
host and deploying the backend, then switches the camera-PC and confirms a new
event. The endpoint accepts one active token at a time, so coordinate the
switch to avoid an event-retry gap.

## JSON contract

The body is a JSON object. Unknown future fields are ignored. The required
stable fields are `schema_version`, `event_id`, `event_type`, `detected_at`,
`vehicle_number`, `camera`, `source`, `stationary_seconds`, and
`confirmation`. The full example also includes optional metadata projections:
`bbox`, `vehicle_roi`, `image`, and `models`.

```json
{
  "schema_version": 1,
  "event_id": "0fa68fe2-6fd8-4cc5-93f7-4b90ae690f19",
  "event_type": "vehicle_plate_detected",
  "detected_at": "2026-08-25T12:30:00.000Z",
  "vehicle_number": "123ABC02",
  "camera": "cam1",
  "source": "main",
  "stationary_seconds": 3.4,
  "confirmation": {
    "votes": 3,
    "detector_confidence": 0.91,
    "ocr_confidence": 0.96
  },
  "bbox": {
    "pixels": [820, 510, 1050, 590],
    "normalized": {"x": 0.320312, "y": 0.354167, "w": 0.089844, "h": 0.055556}
  },
  "vehicle_roi": {
    "coordinate_space": "normalized",
    "points": [
      {"x": 0.38, "y": 0.2},
      {"x": 0.63, "y": 0.32},
      {"x": 0.98, "y": 1.0},
      {"x": 0.18, "y": 1.0}
    ]
  },
  "image": {"width": 2560, "height": 1440},
  "models": {
    "detector": "vehicle-license-plate.pt",
    "ocr": "en_PP-OCRv5_mobile_rec"
  }
}
```

Validation requires schema version `1`, `vehicle_plate_detected`, a UUID
`event_id`, Kazakhstan plate format `123ABC02`, camera `cam<N>`, source `main`
or `sub`, and an ISO 8601 timestamp with a timezone. The `Idempotency-Key`
must match `event_id`.

The webhook returns:

| Situation | Status | Response |
| --- | --- | --- |
| New event saved | 201 | `{ "ok": true, "duplicate": false, "event_id": "…", "vehicle_event_id": 123 }` |
| Same event retried | 200 | `{ "ok": true, "duplicate": true, "event_id": "…" }` |
| Invalid JSON/header/payload | 400 | Normalized error; correct before retrying |
| JSON body exceeds its limit | 413 | Do not retry unchanged; remove unsupported content |
| Missing or wrong token | 401 | No token details are returned |
| Rate limit | 429 | Retry with backoff |
| Temporary backend/database error | 5xx | Retry with the same `event_id` and idempotency key |

Nginx limits this route to a 256 KiB request and five-second upstream/body
timeouts. Django applies the configurable limit (64 KiB by default, never more
than the 256 KiB edge cap) and its dedicated per-source-IP application throttle
before the webhook handler and Bearer-token check. The normal payload is only a
few KiB.

Example request, using a placeholder rather than a real secret:

```bash
curl --fail-with-body --request POST \
  'https://asyl-ltd.kz/api/integrations/vehicle-plate-events' \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <VEHICLE_PLATE_WEBHOOK_TOKEN>' \
  --header 'Idempotency-Key: 0fa68fe2-6fd8-4cc5-93f7-4b90ae690f19' \
  --data @vehicle-plate-event.json
```

The internal CRM journal is available through `GET /api/vehicle-plate-events`
to staff with `events.view`; it supports the documented date, camera, plate and
pagination filters. It contains metadata only.

### Operator diagnostics and ROI

The export-camera tab in `/grain` reads
`GET /api/cameras/cam1/vehicle-plate-runtime/` every five seconds while the tab
is visible; callers need `grain.view`. The first request settles before polling
starts, and each camera-PC probe has a two-second timeout. The backend obtains
the live `/vehicle-number` and
`/cameras/cam1/vehicle-roi` documents from camera-PC, validates them and returns
only a `no-store` projection. Model paths, camera credentials, raw capture
state, active-visit identifiers, recognized plate text and `last_error` never
reach the browser.

The video and AI badges are independent. `ВИДЕО: В ЭФИРЕ` confirms only the
WebRTC stream; the AI badge separately reports whether the detector, shared
OCR, automatic monitor, `cam1/main` assignment, ROI and CRM delivery are ready.
The blue polygon is the current normalized camera-PC ROI, aligned to the actual
`object-cover` video pixels.

The counters are cumulative since the monitor started. Watch which value stops
increasing during a real stopped-truck check, from left to right:

1. `Кадры` increases when the `main` monitor receives frames.
2. `Номера` increases when the vehicle detector finds a plate bbox.
3. `Стоп` increases only after the plate centre is inside ROI and passes the
   stationary gate.
4. `OCR` increases when the cropped plate reaches PaddleOCR.
5. `Готово` increases after the configured matching-vote consensus confirms a
   normalized Kazakhstan plate.

The UI deliberately does not provide ROI editing. Persist geometry through the
camera-PC ROI contract and validate it against a real stopped vehicle before
enabling automatic export.

## Automatic truck-export weighing

Production can apply fresh `cam1` / `main` plate events directly to the truck
export workflow. This is deliberately a fail-closed v1 integration: the camera
payload does not claim whether an observation is an entry or an exit, so the
backend derives the action only from the current, locked CRM state for that
vehicle number.

That inference remains a v1 limitation: the event has no explicit entry/exit
phase or shared visit identifier. It also correlates the two observations by
the OCR-normalized plate, so an OCR mismatch on the second pass cannot safely
close the first trip and may look like a different vehicle. Enable automation
only after both real passes have been validated at this ROI; otherwise keep it
off and use the manual workflow.

This mode relies on one physical-site assumption: the configured `cam1` ROI is
the truck scale itself. The first confirmed stop is the empty truck's entry
weighing; the truck then leaves that ROI to load while remaining on site; its
second distinct stop in the same ROI is the loaded truck's final weighing. It
must not be enabled if `cam1` observes a general driveway, if a truck can leave
and re-enter the ROI merely to reposition, or if the second ROI visit is not
the final weighing. Leaving the entry ROI does not by itself change the CRM
trip to outside or completed.

For an eligible fresh event, the backend performs this sequence:

1. A first `cam1` / `main` event for a plate with no active export trip triggers
   one live read from the configured truck scale. A fresh, stable positive
   reading creates the export, records its entry weight and entry time, and
   leaves the trip on site for loading.
2. A second distinct event for the same plate can become the final weighing
   only when exactly one compatible active export exists and the configured
   minimum trip time has passed. One live scale read records the exit weight;
   it must be greater than the entry weight. The backend then calculates net
   export weight and completes the trip.
3. The event UUID remains the idempotency boundary. Concurrent delivery of the
   same UUID is protected by a short processing lease and cannot start a
   second parallel scale read or create a duplicate trip or weighing. A
   lane-global mutex serializes different plate events on `cam1` / `main`.
   Automatic and explicit operator weighing share two capture barriers: a
   finite Redis lease and a PostgreSQL session advisory lock acquired before
   the live read and held through the atomic apply. PostgreSQL releases the
   advisory lock when its worker connection dies, so Redis TTL expiry cannot
   admit a second reader. Apply transactions also set transaction-local
   `lock_timeout` and `statement_timeout` below the remaining Redis lease
   budget; a blocked write fails closed instead of applying an old sample.

There is no periodic scale polling or automatic scale retry. Each eligible
plate event gets at most one authoritative live scale request for its entire
lifetime. A processed duplicate performs no read. If the scale attempt fails,
the event becomes permanently `manual_required`; retrying the same UUID cannot
read a later value that may belong to another vehicle. Once the freshness
window expires, the backend likewise never associates the current scale value
with the old camera event.

The settings are backend-only environment variables; none belongs in the
browser:

```dotenv
# Django, local Compose and production Compose all default to 0.
# Set 1 on the production host only after the physical preflight below.
VEHICLE_PLATE_AUTO_EXPORT_ENABLED=1
VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME=Отруби
VEHICLE_PLATE_AUTO_EXPORT_EVENT_MAX_AGE_SECONDS=15
VEHICLE_PLATE_AUTO_EXPORT_MIN_TRIP_SECONDS=60
```

`VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME` is the explicit server-side cargo used
for automatically created exports. Configure it to the site's real outgoing
product rather than relying on a frontend form default. The event-age setting
must remain short because `/api/v1/weight` exposes the current scale value, not
a historical sample. The minimum trip interval is a safety guard against
treating an immediate ROI re-entry as a loaded exit. The same interval is also
a cooldown after a completed camera-created export: another event for that
plate during the cooldown is sent to manual review instead of opening a new
trip.

Processing results are recorded on the plate event. Successful actions are
`entry` or `exit`; `manual_entry` identifies the existing operator-selected
fallback, while `ignored` is used for an event outside the configured lane.
Permanent safety failures such as a stale event, ambiguous active trip,
incompatible trip state, entry/exit observations that are too close, or an
exit weight not greater than the entry weight do not mutate the trip and
require operator review. An unavailable, not-ready, stale or malformed scale,
or a capture mutex already owned by another manual or automatic weighing, is
also a one-shot permanent `manual_required` result. The accepted webhook still
returns its normal `201` for a new event or `200` for a duplicate, so the
camera-PC outbox does not retry and accidentally capture another vehicle's
later weight.

A new automatic entry is also blocked while any on-site export has a blank or
unknown plate: that open trip may belong to the newly observed truck. The
event becomes `manual_required` with `unidentified_active_passage`, without a
scale read or trip mutation. An operator must identify, complete or otherwise
safely resolve the existing trip before automation may create another entry.

Only a database/unexpected server failure or a concurrent duplicate of the
same UUID while its original request is still in flight can return a temporary
5xx. An expired processing lease becomes `processing_interrupted` and requires
manual recovery; it is never reclaimed for a second scale read. A different
event that finds the lane/capture mutex busy also goes directly to manual
recovery with a normal successful webhook response.

When `VEHICLE_PLATE_AUTO_EXPORT_ENABLED=0`, webhook ingestion itself remains
successful: new events receive the normal `201`, duplicates receive `200`, and
the events stay available for the existing manual candidate flow. Disabled
automation does not return a retryable error, so the camera-PC outbox does not
retry an already accepted event forever.

The existing manual export form and explicit entry/exit weighing actions are
the recovery path. Operators must use them when automation is disabled, an
event is marked failed, the freshness window has elapsed, the plate is
ambiguous, or the physical route did not follow the assumption above. Manual
recovery must not reuse a stale event's current scale reading.

### Disable or roll back automatic export

1. Set `VEHICLE_PLATE_AUTO_EXPORT_ENABLED=0` in the protected production host
   environment and redeploy/recreate the backend through the normal immutable
   release path. This is the automation kill switch; it does not disable the
   authenticated webhook or the vehicle journal.
2. Confirm new plate events are still stored and that operators can use the
   manual export and weighing controls. Do not stop or reset the camera-PC
   database, bag counter, wagon integration, or truck scale.
3. Leave already accepted events, trips and weighing records intact. Do not
   delete them or reverse their migrations as part of an application rollback.
4. If the application release itself must be rolled back, use the normal image
   rollback described below while keeping the kill switch at `0`. Re-enable
   automation only after the deployed code, `cam1` ROI geometry and both real
   weighing passes have been verified again.

Before explicitly setting `VEHICLE_PLATE_AUTO_EXPORT_ENABLED=1` on the
production host, verify that `cam1/main` covers only the truck scale, the first
pass is the empty entry, the second pass is the loaded final weighing, OCR
returns the same normalized plate on both passes, the truck scale reports a
fresh stable value, there are no unresolved on-site exports with blank plates,
and the manual entry/exit controls still work. Keep the default `0` if any part
of this physical preflight is uncertain.

## Safe rollout and rollback

1. Create a separate production token and put it in the server `.env`; set the
   same value in the camera-PC service through a secure channel.
2. From the server repository, validate without printing interpolated secrets:
   `docker compose -f docker-compose.prod.yml config --quiet`.
3. Deploy with the existing immutable-image workflow. The backend migration is
   applied automatically during its normal startup; verify a new event gets
   `201`, retry it and verify `200`, then check the CRM journal.
4. If the release health gate fails, the existing deploy workflow restores the
   prior application image and checkout automatically. The database is not
   automatically restored; the migration is additive and the existing backup
   procedure remains available for a deliberate recovery.

An application rollback leaves vehicle event rows and the new table intact so
that no accepted audit data is discarded. Removing that table or its rows is a
separate, explicit maintenance operation: first take and verify a database
backup, stop the camera-PC sender, use the exact reverse migration approved for
the deployed release, then verify the result. It must never be part of the
normal release rollback. Neither path changes bag counters/events, wagon
integration, or the camera-PC SQLite database.

To disable this integration without touching other camera services, stop sends
from the camera-PC and rotate the production token to a new unused value, then
redeploy. Production Compose intentionally refuses a missing token. Existing
vehicle events remain in the CRM. Do not reset the camera-PC SQLite database or
its counters as part of this rollback.
