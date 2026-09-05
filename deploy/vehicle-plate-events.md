# Vehicle plate recognition: production integration

The active export workflow is **weight-first**: Asyl LTD reads one stable truck
scale value and only then asks the camera-PC to recognize the vehicle in the
saved ROI. The older camera-first webhook remains documented below for audit
history and rollback compatibility, but it must not drive the same physical
lane at the same time.

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
`event_id`, Kazakhstan plate format `123ABC02`, the two-letter series `160AL17` or the 1993 format `X209LAN`, camera `cam<N>`, source `main`
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

The export-camera tab in `/grain` reads the configuration-aware bootstrap
`GET /api/cameras/vehicle-plate-runtime/` every five seconds while the tab
is visible; callers need `grain.view`. The first request settles before polling
starts, and each camera-PC probe has a two-second timeout. The backend obtains
the configured weight-first camera and source from its server-only settings,
then obtains the live `/vehicle-number` and matching
`/cameras/<cam>/vehicle-roi` documents from camera-PC. It validates them and
returns a `no-store` projection including the safe logical browser stream
(`camN` for `sub`, `camNmain` for `main`). Model paths, camera credentials, raw capture
state, active-visit identifiers, recognized plate text and `last_error` never
reach the browser.

The video and AI badges are independent. `ВИДЕО: В ЭФИРЕ` confirms only the
WebRTC stream; the AI badge separately reports whether the detector, shared
OCR, configured on-demand camera/source and ROI are ready. The editor uses the
logical browser alias returned by the backend, so a configured `cam7/sub` lane
renders `cam7`, while `cam7/main` renders `cam7main`; both edit the ROI that OCR
will actually use.
The blue polygon is therefore
aligned to the exact `object-cover` video pixels evaluated by the model.

The counters are cumulative since the monitor started. Watch which value stops
increasing during a real stopped-truck check, from left to right:

1. `Кадры` increases when the `main` monitor receives frames.
2. `Номера` increases when the vehicle detector finds a plate bbox.
3. `Стоп` increases only after the plate centre is inside ROI and passes the
   stationary gate.
4. `OCR` increases when the cropped plate reaches PaddleOCR.
5. `Готово` increases after the configured matching-vote consensus confirms a
   normalized Kazakhstan plate.

Superusers can edit the same polygon directly over the export-camera video.
The browser sends normalized points to
`PUT /api/cameras/<configured-cam>/vehicle-plate-runtime/`; the backend validates
the 3–12-point, non-degenerate polygon, fixes its source to the configured
`VEHICLE_PLATE_WEIGHT_FIRST_SOURCE`, and proxies it to the canonical camera-PC
`PUT /cameras/<configured-cam>/vehicle-roi` contract. The camera-PC persists the geometry
atomically and asks the running monitor to reload it immediately. A `503` with
`code=roi_saved_refresh_pending` means the file was saved but the live monitor
could not confirm the immediate refresh; the monitor's normal two-second ROI
reload still picks it up after recovery. All GET and PUT responses are
`no-store`, and non-superusers cannot change the polygon.

## Weight-first truck-export weighing (manual trigger)

The operator creates an export passage first. The existing commands remain the
only business trigger:

```http
POST /api/grain/wagons/<wagon-id>/entry-weight/
POST /api/grain/wagons/<wagon-id>/exit-weight/
Content-Type: application/json
Idempotency-Key: <canonical lowercase UUID>

{}
```

The browser generates one UUID per button attempt and keeps it in
`sessionStorage` until the server gives a certain terminal response. A network
retry and an auth-token refresh preserve the same UUID. The read-only scale
preview endpoint never starts OCR or changes accounting state.

For a new UUID the backend executes this sequence:

1. Commit a `PassageWeightCapture` claim before hardware I/O.
2. Acquire the shared truck-scale capture mutex and read the physical scale
   exactly once. The sample must already be connected, stable, fresh and
   positive.
3. Store the weight and its stable timestamp on the durable claim, without yet
   changing `Wagon` or creating `WeighingRecord`.
4. POST the same UUID and timestamp to camera-PC:

   ```http
   POST /cameras/cam1/vehicle-recognition
   X-Api-Key: <AI_SERVICE_API_KEY>
   Idempotency-Key: <same UUID>
   Content-Type: application/json

   {"stable_weight_at":"2026-08-30T10:21:14.381000Z"}
   ```

   Weight is deliberately not sent. The camera source comes from the saved
   ROI. Camera-PC fences off old frames, requires exactly one plate bbox inside
   that ROI and confirms the same normalized Kazakhstan number with repeated
   OCR votes.
5. In one database transaction Asyl locks the claim and trip, verifies the
   current phase and number, writes the plate, `WeighingRecord`, weight/net and
   all Wagon status transitions, then marks the claim `completed`. Any business
   rejection rolls back all accounting changes and leaves an auditable failed
   claim.

Entry may fill an empty trip number or confirm an already entered identical
number. Exit must recognize the same number saved at entry. A mismatch, missing
ROI, no OCR consensus, invalid plate or exit weight not greater than entry is a
terminal failure: no partial weight, number or status is saved.

If the response is lost after camera-PC received the command, the claim stays
retryable. A later request with the same UUID never reads the scale again and
uses the lookup-only endpoint:

```http
POST /cameras/cam1/vehicle-recognition-retry
Idempotency-Key: <same UUID>

{"stable_weight_at":"<same timestamp>"}
```

This endpoint can replay `processing` or the cached terminal result but cannot
create a camera claim. If the original POST never arrived, camera-PC atomically
persists a terminal tombstone. A delayed original POST then sees that tombstone
and cannot capture another vehicle. An interrupted Asyl claim that never saved
a scale sample also cannot be resumed and requires a new deliberate attempt.

Camera-PC may answer `202` with `status=processing` while the first request is
still running. This is not a new capture: Asyl polls only the retry endpoint
with the same `Idempotency-Key` and identical `stable_weight_at` until it
receives a terminal response. Operators must not generate another UUID for
that physical weighing. Browser/proxy `502`/`503`/`504` responses switch the
same durable Asyl claim to this retry-only path.

Both hosts must synchronize time (NTP/Windows Time). Camera-PC rejects a new
trigger that is over the configured max age or more than five seconds in the
future; replay of an already claimed UUID is checked before this freshness
gate. The on-demand timeout bounds the frame-scanning window, not arbitrary
driver shutdown or a single non-cancellable OCR call, so the Asyl HTTP timeout
must include a small transport/cleanup margin.

The Wagon detail response exposes safe capture audit fields under
`vehicle_recognition_captures`: request UUID, action, stage, weight, plate,
confidence, response status and sanitized error. Raw images are neither sent
to Asyl nor stored there.

Backend production settings:

```dotenv
AI_SERVICE_URL=http://<TAILSCALE-IP-CAMERA-PC>:8890
AI_SERVICE_API_KEY=<same 32-512 character plaintext key whose SHA-256 is on camera-PC>
VEHICLE_PLATE_AUTO_EXPORT_ENABLED=0
VEHICLE_PLATE_WEIGHT_FIRST_ENABLED=1
VEHICLE_PLATE_AUTO_SCALE_ENABLED=0
VEHICLE_PLATE_WEIGHT_FIRST_CAMERA=cam1
VEHICLE_PLATE_WEIGHT_FIRST_SOURCE=main
VEHICLE_PLATE_WEIGHT_FIRST_TIMEOUT_SECONDS=12
```

Legacy camera-first mode cannot run together with either weight-triggered
mode. Manual weight-first and automatic scale polling may coexist because both
share the physical-scale mutex and Camera-PC idempotency contract. On
camera-PC the matching lane must be on-demand only:

```dotenv
AI_VEHICLE_AUTO_ENABLED=false
AI_VEHICLE_AUTO_CAMERAS=
AI_VEHICLE_ON_DEMAND_CAMERAS=cam1
```

`VEHICLE_PLATE_WEIGHT_FIRST_SOURCE` must match the saved camera-PC ROI source.
The browser stream is derived safely from the provisioned go2rtc convention:
`sub` maps to `<camera>` and `main` maps to `<camera>main` (for example,
`cam7` or `cam7main`). It is not a separately trusted client setting. Camera
slots are deliberately limited to `cam1..cam32`, matching the static aliases.

The protected production `.env` is the source of these non-secret toggles.
`AI_SERVICE_API_KEY` remains backend-only. Rollback to plain manual weighing
sets all three mode flags to `0`; it does not remove capture audit rows or
migrations.

## Automatic scale-first truck export (default off)

`passage-scale-monitor` is a dedicated sequential process. It polls the truck
scale every second, but a numeric change is never itself a business trigger.
The PostgreSQL state machine starts `unarmed` and requires several consecutive
fresh, stable readings at or below the configured empty threshold. It then
requires an occupied weight to remain fresh, stable, and within the configured
tolerance for the durable `stable_weight_seconds` interval (10 seconds by
default). Only after that real elapsed interval does it commit one
`AutomaticPassageCapture`, perform one strict scale read, and call the same
on-demand Camera-PC endpoint with that capture UUID. Empty, unsafe, changed, or
failed observations reset the complete interval; a monitor restart also fences
the candidate and requires a new confirmed clear edge.

After OCR, locked CRM state determines the action. A plate with no on-site
passage creates an export passage and records its empty entry weight
(`arrived -> at_silo`). A plate that matches a passage still waiting for its
empty weight (for example one a dispatcher registered by hand) records the
entry into that passage. The same plate on exactly one on-site passage that
already carries an entry weight records the loaded exit weight and completes
the status chain (`at_silo -> ... -> completed`). An unknown plate is always a
new entry, even while blank-number or manual passages are on site: automation
never stops to ask whether a human mistyped a plate.

Camera-PC answers `no_match` after one 8-second window, but trucks stand on
the scale for 30-60 seconds. The monitor therefore asks again: each attempt
re-reads the strict scale (the weight must still match the stored sample
within tolerance) and sends a new Camera-PC request whose UUID is
`uuid5(capture UUID, "attempt-N")`, up to
`VEHICLE_PLATE_AUTO_SCALE_MAX_RECOGNITION_ATTEMPTS` attempts. Configuration
failures (missing ROI, model, key, camera) skip the retries.

When the plate is still unknown the weighing is applied without a number
instead of waiting for an operator: with no open passage on site a passage
with an empty number is created and weighed (the operator fills in the plate
later from the wagon card, where a **номер не распознан** badge and the photo
help); with open passages on site the weight is parked as an
**unassigned weighing** (`/api/grain/unassigned-weighings/`) together with its
photo, and the Grain page shows a panel where a `grain.weigh` operator binds
it to a passage (entry or exit), opens a new passage from it, or discards it.
Either way the lane goes to `awaiting_clear` and re-arms by itself after the
confirmed clear streak.

`manual_required` with the **Подтвердить ручную обработку** acknowledgement
remains only for failures that happened while writing the business result
(database apply errors). Scale-read failures before a sample was stored and
recognition failures never latch the lane: `requires_acknowledgement=false`
on the capture, the lane re-arms after a fresh confirmed clear. `stale`,
disconnected, malformed, unstable, or `weight_kg=null` responses never count
as an empty scale. Thus a restart while a truck is parked cannot duplicate it,
and an unattended error cannot disappear between five-second UI polls.

Every completed capture (recognized or plate-less) then fetches the evidence
frame Camera-PC kept for the last attempt
(`GET /cameras/<cam>/vehicle-recognition/<uuid>/frame`) and stores it on the
`WeighingRecord` (or the unassigned weighing) under `MEDIA_ROOT/grain/`. The
wagon detail exposes `entry_photo_url`/`exit_photo_url` as signed links valid
for one hour (`/api/grain/photos/<kind>/<id>/?token=...`); the media
directory itself is never served by nginx. A missing photo never changes the
weighing. The manual weight-first button stores the photo the same way.

The CRM polls
`GET /api/grain/automatic-passage-scale/runtime/` independently from
Camera-PC, so a camera diagnostics outage cannot hide `recognizing`,
`applying`, or a latched `manual_required` state. The response exposes only a
safe operation UUID, stage, action, wagon ID, retry flag, and bounded error
code—never the recognized plate, weight, upstream address, or raw payload.
The same response includes the active `stable_weight_seconds`. Grain viewers
may read the durable value through
`GET /api/grain/automatic-passage-scale/settings/`; only a superuser may change
it with an exact integer from 2 through 60 via `PATCH` or `PUT`. The Camera Gate
screen polls this setting and exposes the editor only to a superuser. Changing
it while a candidate is stabilizing resets that candidate, so the newly chosen
full interval must pass before OCR.
Turning the kill switch off stops and terminalizes new work, releases a
completed/acknowledged lane for manual controls, and still keeps an unacknowledged
failure visible until the operator confirms it.

Recovery never obtains a later physical sample: an interrupted claim without
a stored weight becomes terminal, a stored `recognizing` capture calls only
`vehicle-recognition-retry` with the original UUID/timestamp, and an
`applying` capture reuses the stored event. The final strict weight must still
match the observed candidate within tolerance before Camera-PC is contacted.
If the process dies during the final allowed camera call, its unknown outcome
gets one lookup-only retry before the attempt is declared exhausted.

```dotenv
VEHICLE_PLATE_AUTO_EXPORT_ENABLED=0
VEHICLE_PLATE_AUTO_SCALE_ENABLED=1
VEHICLE_PLATE_AUTO_SCALE_POLL_SECONDS=1
VEHICLE_PLATE_AUTO_SCALE_EMPTY_MAX_KG=500
VEHICLE_PLATE_AUTO_SCALE_STABLE_CONFIRM_POLLS=2
VEHICLE_PLATE_AUTO_SCALE_CLEAR_CONFIRM_POLLS=3
VEHICLE_PLATE_AUTO_SCALE_STABLE_TOLERANCE_KG=50
VEHICLE_PLATE_AUTO_SCALE_MAX_RECOGNITION_ATTEMPTS=3
VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS=60
```

`VEHICLE_PLATE_AUTO_SCALE_STABLE_CONFIRM_POLLS` remains accepted only for
configuration compatibility. It no longer controls the occupied trigger; the
durable UI/API setting `stable_weight_seconds` is authoritative.

The heartbeat maximum age must cover the configured poll interval, preview
and strict scale timeouts, Camera-PC timeout, and database apply margin;
startup rejects a shorter self-defeating value. Active passage deletion takes
the same persistent lane mutex as episode claiming, so an in-flight loaded
exit cannot race deletion and become a false new entry. A manually created
passage waiting for its entry weight, and any durable manual weight-first
capture still in `processing`, reserve the same physical lane; empty polls
during operator or OCR latency therefore cannot arm a competing automatic
episode. While a manually owned passage remains `at_silo`, an otherwise
unknown plate is also never classified as a new automatic entry: it stops at
`manual_required`, because the stored manual plate may contain a typo.

The kill switch defaults to `0`. Before enabling it, verify that physically
empty scales produce fresh stable zero/low readings. The controller currently
seen on site may return `stale=true`, `weight_kg=null` while empty; that is
safely reported as unavailable and will never arm automation. Update that edge
behavior (or add a durable `stable_episode_id`) and validate a full
empty-entry-clear-loaded-exit-clear rehearsal before rollout.

## Legacy camera-first truck export (disabled in weight-first mode)

The following section describes the older webhook-driven implementation. Keep
`VEHICLE_PLATE_AUTO_EXPORT_ENABLED=0` when weight-first is enabled. It is
retained only for historical event ingestion and an explicit rollback; do not
run both coordinators on `cam1`.

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

1. Choose one lane, for example `cam7/sub`. Set the same lane in Asyl through
   `VEHICLE_PLATE_WEIGHT_FIRST_CAMERA=cam7` and
   `VEHICLE_PLATE_WEIGHT_FIRST_SOURCE=sub`; deploy camera-PC with
   `AI_VEHICLE_ON_DEMAND_CAMERAS=cam7`, the vehicle model, shared OCR and a
   saved `cam7` ROI whose source is `sub`. Keep its continuous vehicle sender
   off.
2. From the server repository, validate without printing interpolated secrets:
   `docker compose -f docker-compose.prod.yml config --quiet`.
3. Deploy Asyl first with `AUTO_EXPORT=0`, `WEIGHT_FIRST=1` and
   `AUTO_SCALE=0`. The additive capture/state migration runs during normal
   startup. On a test vehicle, verify the manual weight-first path uses one
   scale read, the saved number matches the camera and its capture is
   `completed`; repeat the same request UUID and verify no second weighing.
4. Verify the empty scale produces fresh stable low readings, then rehearse the
   automatic process in a controlled window: clear -> empty entry -> clear ->
   loaded exit -> clear. Only then set `VEHICLE_PLATE_AUTO_SCALE_ENABLED=1`.
   Confirm the UI returns to `ОЖИДАЕТ МАШИНУ` and each physical occupancy owns
   exactly one automatic capture. Disable this one flag immediately if the UI
   reports `НУЖЕН ОПЕРАТОР` unexpectedly.
5. If the release health gate fails, the existing deploy workflow restores the
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

To disable weight-first without touching bag counting, set
`VEHICLE_PLATE_WEIGHT_FIRST_ENABLED=0` and recreate only the normal application
release. Existing vehicle events and capture audit remain in the CRM. Do not
reset the camera-PC SQLite database or its counters as part of this rollback.
Automatic polling has an independent immediate business kill switch:
`VEHICLE_PLATE_AUTO_SCALE_ENABLED=0`.

## Camera orientation: front = entry, rear = exit

The scale camera looks along the truck scale. A truck that faces it is
driving in (empty, about to be loaded); a truck that shows its tail is driving
out (loaded). Since 2026-09-05 the Camera-PC runs a small front/rear
classifier (`models/vehicle-orientation.pt`, `yolo11n-cls`, classes
`front,rear`) on the same ROI crop the plate detector scans and returns
`orientation: {label, confidence, raw_label}` with both `recognized` and
`no_match` answers. `label` is `null` below
`AI_VEHICLE_ORIENTATION_CONFIDENCE_THRESHOLD` (0.60) or when the model is
absent, and the CRM then falls back to the older weight/state rules.

The verdict is stored on `AutomaticPassageCapture.orientation` (+ confidence),
on every `WeighingRecord.orientation` and on `UnassignedWeighing.orientation`,
and is the primary entry/exit signal in `apps/grain/services.py`:

- **rear, plate known, no open trip**: the empty entry was missed. The latest
  parked front-facing (or, without a verdict, lighter) unassigned weighing of
  the last `VEHICLE_PLATE_AUTO_MISSED_ENTRY_MAX_AGE_HOURS` (24) becomes the
  entry of a new trip and the current weight closes it. With nothing parked the
  weight is stored as an unassigned weighing with `reason=entry_missing` and
  the plate in `vehicle_number`; the panel prefills that plate for a new trip.
- **front, plate known, trip still open**: the loaded exit was missed. A parked
  rear-facing (or heavier) weighing inside that trip closes it; otherwise the
  stale trip is cancelled with an `exit_note`, and a fresh trip takes this entry.
- **rear, plate not recognized**: exactly one on-site trip waiting for a
  heavier loaded weight is closed automatically; several candidates park the
  weight (`open_passages_exist`), none parks it as `entry_missing`. A rear
  weight never opens a trip any more, even on an empty site.
- **front, plate not recognized**: always a new blank-number trip, even while
  other trips are open.
- Plates that differ by one dropped series letter (`849AT13` vs `849ATT13`)
  are the same truck when exactly one on-site trip is compatible.

Operators repair a trip whose booked "entry" was really the exit from the
unassigned panel: binding an earlier, lighter (or front-facing) weight to an
`at_silo` trip swaps it into the entry, re-labels the booked weight as the
exit and completes the trip.

The classifier retrains itself. Every night at 01:30 (Almaty) the celery beat
task `grain.export_orientation_samples` (`apps/grain/orientation_dataset.py`,
manual: `manage.py export_orientation_samples [--collect-only] [--limit N]`)
labels every recent frame with a photo and posts new or relabelled ones to
Camera-PC (`POST /vehicle-orientation/samples`):

- a completed trip is the ground truth: its entry frame is `front`, its exit
  frame `rear`, whatever the truck weighs (so a 9 t empty KAMAZ is still an
  entry);
- a frame without a closed trip is labelled by weight only when it is clear:
  below `VEHICLE_ORIENTATION_EMPTY_MAX_KG` (5000) is `front`, above
  `VEHICLE_ORIENTATION_LOADED_MIN_KG` (6000) is `rear`, in between is skipped;
  weights of cancelled trips are never used;
- a frame the classifier itself was confidently wrong about is held back as a
  `conflict` in `VehicleOrientationSample` for a human look, so the loop never
  learns from its own mistakes;
- an operator correction (the missed-entry swap, a re-assigned weighing)
  changes the label and the frame is sent again, which relabels it on the PC.

Reviewing the dataset: the CRM page **Датасет ориентации** (`/grain/orientation`,
sidebar under «Приход и вывоз»; read with `grain.view`, edit with `grain.admin`)
shows every frame with its label, source (по рейсу / по весу / вручную), the
classifier's contradicting verdict on conflicts, and the Camera-PC training
report (`GET /api/grain/orientation-samples/summary/`, cached 30 s, probe
timeout 2 s). «Передом»/«Задом» sets a manual label that automatic relabelling
never overrides and re-sends the frame; «Исключить» drops it and, when Camera-PC
already holds a copy, removes it there (`DELETE /vehicle-orientation/samples/<id>`).

Camera-PC keeps the frames in `orientation-dataset/` and at 02:30 local runs
`ASYL-AI-Orientation-Training`: ROI crops, a deterministic 20 % hold-out,
fine-tuning of the current model, and promotion to
`models/vehicle-orientation.trained.pt` only if the candidate is at least as
accurate as the model in service (and at least 0.95 with every class recall at
least 0.90). The service reloads the promoted file on the next recognition
without a restart; `GET /vehicle-orientation` (and the `orientation` block of
`/api/cameras/vehicle-plate-runtime/` via `ai.vehicle_orientation_info()`)
shows dataset counts and the last training report. Deleting the trained file
on the PC returns to the shipped base model. `VEHICLE_ORIENTATION_DATASET_ENABLED=0`
stops the export.

## Late bag events after a posted shift

A Camera-PC restart-gap backfill can deliver bag events whose shift is already
posted to stock. Since 2026-09-05 `apps/cameras/event_sync.py` no longer
refuses such a page (which froze the cam3 journal and failed every deploy
health gate): the bag is counted in the still-open daily analytics, the
imported row keeps `applied_to_production=False`, and a warning names the
camera and count. The posted batch is never mutated.
