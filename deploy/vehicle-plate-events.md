# Vehicle plate recognition: production integration

The export workflow is **weight-first**: Asyl LTD reads one stable truck
scale value and only then asks the camera-PC to recognize the vehicle in the
saved ROI. The webhook below only stores plate events; it never reads the scale
or changes a trip.

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
`confirmation`. The full example also includes optional metadata
(`bbox`, `vehicle_roi`, `image`, `models`); the CRM accepts it but stores only
the validated stable fields.

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
POST /api/grain/passages/<wagon-id>/entry-weight/
POST /api/grain/passages/<wagon-id>/exit-weight/
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
VEHICLE_PLATE_WEIGHT_FIRST_ENABLED=1
VEHICLE_PLATE_AUTO_SCALE_ENABLED=0
VEHICLE_PLATE_WEIGHT_FIRST_CAMERA=cam1
VEHICLE_PLATE_WEIGHT_FIRST_SOURCE=main
VEHICLE_PLATE_WEIGHT_FIRST_TIMEOUT_SECONDS=12
```

Manual weight-first and the automatic collector may coexist because both
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
sets both mode flags to `0`; it does not remove capture audit rows or
migrations.

## Automatic scale-first truck export (default off)

The independent weighbridge collector (`deploy/weighbridge/README.md`) owns
physical polling, the stable-weight edge, re-arming and the Camera-PC OCR call.
It commits every capture with its weight, UUID, photo and recognition result to
its SQLite outbox. `passage-scale-monitor` (`manage.py monitor_passage_scale`)
imports that outbox once the durable marker `/var/lib/weighbridge/enabled`
exists; without the marker it reports `disabled` and never reads the scale. The
same process runs bounded background workers for photo delivery, plate
identity and the wagon arch. The importer replays each capture idempotently by
its UUID: a recognized plate is applied to its trip, a capture without a usable
plate is applied as an unidentified weighing, and a business conflict is parked
for the operator.

A new front-facing truck opens an entry, including a blank-number entry when
only the direction was recognized. An exact recognized plate can close its
open trip when the time, state and loaded weight are valid. Missing direction
for a new plate, unreadable exits, missing entries and business-state conflicts
become **unassigned weighings**. One blank trip, one lighter parked weight or
one similar plate never proves identity. A new front read does not cancel an
unfinished trip or automatically borrow a parked exit weight.

The **Неопознанные взвешивания** panel and the trip card let an operator choose
a saved weight, review the photo/time/gross/net, and assign it without reading
the physical scale or OCR again. The **Журнал взвешиваний** is read-only for
`grain.view`: `/api/grain/automatic-passage-scale/history/` returns the last
50 automatic attempts and a `before` cursor for older ones. It includes
processing, assigned/unassigned, and failed attempts, including departure
before stability or a scale observation outage. An observed candidate weight
is never presented as a confirmed weighing.

Every imported sample creates a `WeighingPhotoDelivery` that keeps the frame
the collector shipped with it. A later truck is never photographed to fill a
missing frame: the worker fetches only
`GET /cameras/<cam>/vehicle-recognition/<uuid>/frame`, retrying after
5/15/60/300 seconds and then every 30 minutes for up to seven days. Photo
failures cannot undo a weight. Each delivery is leased in the database, can
survive restart, and re-resolves its target after network I/O so operator
assignment cannot strand the photo or overwrite a weight. Saved evidence from
another attempt of the same capture can be reused. The manual weight-first
path uses the same UUID delivery queue. `passage-scale-monitor` runs the photo
worker on every loop, so delivery recovers even when automatic weighing is
disabled.

Photo statuses are `pending`, `retrying`, `saved`, `unavailable`. Photos stay
under private `MEDIA_ROOT/grain/` and use one-hour signed URLs; there is no
public media directory. Migration 0015 queues recent existing records with
missing photos and known Camera-PC request IDs. It performs no network I/O.
Frames already missing from both CRM and Camera-PC cannot be reconstructed.

The CRM polls
`GET /api/grain/automatic-passage-scale/runtime/` independently from
Camera-PC. It projects the collector heartbeat the monitor imports and falls
back to the durable lane state when that heartbeat is stale. The response
exposes only a safe operation UUID, stage, action, wagon ID, retry flag, and bounded error
code—never the recognized plate, weight, upstream address, or raw payload.
The same response includes the active `stable_weight_seconds`. Grain viewers
may read the durable value through
`GET /api/grain/automatic-passage-scale/settings/`; only a superuser may change
it with an exact integer from 2 through 60 via `PATCH`. The Camera Gate
screen shows the value from the runtime poll and exposes the editor only to a
superuser. The monitor hands a changed value to the collector through its
outbox on the next poll.
`monitor_passage_scale --once` runs one import pass and one wagon-arch pass.

```dotenv
VEHICLE_PLATE_AUTO_SCALE_ENABLED=1
VEHICLE_PLATE_AUTO_SCALE_POLL_SECONDS=1
VEHICLE_PLATE_AUTO_SCALE_EMPTY_MAX_KG=500
VEHICLE_PLATE_AUTO_SCALE_CLEAR_CONFIRM_POLLS=3
VEHICLE_PLATE_AUTO_SCALE_STABLE_TOLERANCE_KG=50
VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS=60
```

The heartbeat contract retains its existing timeout margin. Manual physical
operations share the lane mutex and cannot race unfinished automatic samples.
Registering a passage ahead of time does not reserve the scale indefinitely;
automation can record its entry when its plate is recognized.

The kill switch defaults to `0`. Before enabling it, verify that physically
empty scales produce fresh stable zero/low readings. The controller currently
seen on site may return `stale=true`, `weight_kg=null` while empty; that is
safely reported as unavailable and will never arm automation. Update that edge
behavior (or add a durable `stable_episode_id`) and validate a full
empty-entry-clear-loaded-exit-clear rehearsal before rollout.

## Export trip settings

```dotenv
VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME=Отруби
VEHICLE_PLATE_AUTO_EXPORT_MIN_TRIP_SECONDS=60
```

`VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME` is the explicit server-side cargo used
for automatically created exports. Configure it to the site's real outgoing
product rather than relying on a frontend form default. The minimum trip
interval is a safety guard against treating an immediate re-weighing as a
loaded exit. The same interval is also a cooldown after a completed automatic
export: another weighing for that plate during the cooldown is sent to manual
review instead of opening a new trip.

## Safe rollout and rollback

1. Choose one lane, for example `cam7/sub`. Set the same lane in Asyl through
   `VEHICLE_PLATE_WEIGHT_FIRST_CAMERA=cam7` and
   `VEHICLE_PLATE_WEIGHT_FIRST_SOURCE=sub`; deploy camera-PC with
   `AI_VEHICLE_ON_DEMAND_CAMERAS=cam7`, the vehicle model, shared OCR and a
   saved `cam7` ROI whose source is `sub`. Keep its continuous vehicle sender
   off.
2. From the server repository, validate without printing interpolated secrets:
   `docker compose -f docker-compose.prod.yml config --quiet`.
3. Deploy Asyl first with `WEIGHT_FIRST=1` and `AUTO_SCALE=0`. The additive
   capture/state migration runs during normal startup. On a test vehicle,
   verify the manual weight-first path uses one scale read, the saved number
   matches the camera and its capture is `completed`; repeat the same request
   UUID and verify no second weighing.
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
Automatic weighing has an independent immediate business kill switch:
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
absent. A scale-triggered new plate without a verdict is retained for review;
an exact existing plate can still use the trip state.

The verdict is stored on `AutomaticPassageCapture.orientation` (+ confidence),
on every `WeighingRecord.orientation` and on `UnassignedWeighing.orientation`,
and is the primary entry/exit signal in `apps/grain/services.py`:

- **rear, plate known, no exact open trip**: save an unassigned exit with
  `reason=entry_missing`, its plate and its evidence.
- **front, plate known, trip still open**: preserve the old trip and park the
  new weight with `reason=open_trip_conflict` for review.
- **rear, plate unreadable**: park the weight even if only one trip is open.
- **front, plate unreadable**: create a blank-number entry.
- **direction and plate unknown**: park the weight; an empty site does not
  establish that it was an entry.
- Different series letters, including a dropped letter, require operator
  confirmation. Time and weight only help the operator compare candidates.

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

Reviewing the dataset: the CRM page **Датасет ориентации** is an owner-only
tool, reached by the direct URL `/grain/orientation` (no sidebar link); the API
(`/api/grain/orientation-samples/`) is superuser-only and answers 403 to every
employee, whatever their `grain.*` permissions. The page shows every frame with
its label, source (по рейсу / по весу / вручную), the classifier's
contradicting verdict on conflicts, and the Camera-PC training report
(`GET /api/grain/orientation-samples/summary/`, cached 30 s, probe timeout
2 s). «Передом»/«Задом» sets a manual label that automatic relabelling never
overrides and re-sends the frame; «Исключить» drops it and, when Camera-PC
already holds a copy, removes it there (`DELETE /vehicle-orientation/samples/<id>`).
A row remembers two things separately: `sent_at` (the current label was
delivered; reset by a relabel so the frame goes again) and `delivered_at` (the
PC holds a copy; cleared only when the PC confirms the removal). Only
`delivered_at` decides whether a purge has to contact the PC, so a relabelled
frame is never deleted from the CRM while its old copy stays on the PC.

Purging the dataset once the model has learnt from it: «Очистить датасет…» on
the page calls `POST /api/grain/orientation-samples/purge/` with
`{older_than_days: null}` (everything, one `DELETE /vehicle-orientation/samples`
on the PC) or `{older_than_days: N}` (frames captured more than N days ago,
removed one by one). Every call handles at most `PURGE_BATCH` (100) rows so it
fits the 60 s nginx timeout and answers
`{deleted, removed_from_pc, pc_unavailable, remaining}`; the page repeats the
request while `remaining > 0` (it stops when `pc_unavailable` is set — the next
batch would meet the same rows). The same job from the shell:
`manage.py purge_orientation_samples --all|--older-than-days N [--keep-pc]`
loops over the batches itself and prints the totals; `--keep-pc` deletes CRM
rows only. Weighing photos are never touched — they are the evidence of the
trip. A purge also moves the collection watermark
(`VehicleOrientationDatasetState.collect_since`: «now» for `--all`, the cutoff
for `--older-than-days`), so the nightly `collect()` never re-labels and
re-uploads frames from a purged period, however young they are; the watermark
only ever moves forward. If the Camera-PC is down during a purge, rows it still
holds are kept in the CRM as excluded with `removal_pending`: the nightly
`export_removals` asks the PC to forget them once it is back, and until the
next purge those rows stay excluded (they are never trained on and never
re-collected).

Camera-PC keeps the frames in `orientation-dataset/` and at 02:30 local runs
`ASYL-AI-Orientation-Training`: ROI crops, a deterministic 20 % hold-out,
fine-tuning of the current model, and promotion to
`models/vehicle-orientation.trained.pt` only if the candidate is at least as
accurate as the model in service (and at least 0.95 with every class recall at
least 0.90). The service reloads the promoted file on the next recognition
without a restart; `GET /vehicle-orientation` (and the `camera_pc` field of the
superuser-only `GET /api/grain/orientation-samples/summary/` via
`ai.vehicle_orientation_info()`, cached for 30 s) shows dataset counts and the
last training report. Deleting the trained file
on the PC returns to the shipped base model. `VEHICLE_ORIENTATION_DATASET_ENABLED=0`
stops the export.

## Automatic routing of parked weighings (since 2026-09-22)

Every collector weight is parked as an `UnassignedWeighing`
(`reason=identity_verification_required`) and booked by
`apps/grain/automatic_routing.py::book(item, plate, orientation)` once the
plate is known (Camera-PC OCR, or one single-frame `gpt-5-mini` read when OCR
gave nothing bookable). Only deterministic code writes weights; the model
supplies identity. Rules that decide where a parked weight goes:

- **Re-entry while the plate's visit is still open** (`_unseen_departure`):
  the previous exit was missed. Candidates are the loaded rear weighings parked
  between that entry and the re-entry, no later than `entry + 12 h`
  (`WEIGHING_AI_ENTRY_MAX_HOURS`, the longest trip) and at least
  `MIN_LOADED_GAIN_KG` (1000) heavier than the entry. A candidate fits when it
  was read (OCR / raw model text, weak camera votes excluded) within two edits
  of the plate, or when nobody read a plate on it at all and its identity check
  has ended (`review`); a plate-less candidate whose check is still pending
  freezes the decision. Exactly one candidate closes the old visit and the
  re-entry opens the next one. Two or more: the re-entry parks as
  `previous_exit_missing` (retried every 30 s, the operator binds the exit).
  None and the visit is older than 12 h: the old visit is `cancelled` with
  `exit_note` «Выезд не зафиксирован: рейс закрыт автоматически при новом
  заезде» and the new one opens. None and 30 min – 12 h: `previous_exit_missing`.
  Under 30 min without a candidate: the same visit, re-weighed at the gate.
- **Re-entry under the right plate while a visit is open under a misread
  spelling** (`_settle_similar_visit`): one edit away or the same plate core
  (`E065CUA` ↔ `065CUA13`, region or leading letter dropped), exactly one such
  visit, and the empty weight within `TARE_TOLERANCE_KG` (300) of that visit's
  entry. It is closed only by an exit read as this plate (unread exits may be a
  neighbour's: 261BBF13 and 411BBF13 share the site), or cancelled after 12 h
  when the spelling has the same core. The entry under the correct plate is
  created in any case.
- **Exit read one or two edits off a parked entry of the same truck**
  (`_earlier_entry_pending`): waits (`earlier_entry_pending`) instead of
  closing an older visit.
- **Exit with a plate and no open visit** (`_parked_entry`): before reusing the
  remembered tare, the single unread front weighing of the last 12 h whose
  check has ended, that nobody read as another truck and whose weight is within
  300 kg of this plate's `VehicleTareMemory` becomes the real entry (event
  «заезд восстановлен из неопознанного взвешивания»). Without tare memory or
  with several fitting weighings the historical tare is used as before
  (`saved_tare_missing` when there is none).
- **Exit with a plate whose visit is open under a misread front plate**
  (`_orphan_visit`, looked up together with `_parked_entry`): the front
  camera invented a spelling (253ZOU81 for 532OUB13, E065CUA for 065CUA13)
  that no exit can ever name, so the visit would stay open for good while
  the real exit took a tare from history. When this plate has a
  `VehicleTareMemory`, exactly one open visit entered within the last 12 h
  fits — the core of its plate (region and the old form's leading letter
  dropped, see `_plate_core`) shares at least `ORPHAN_PLATE_OVERLAP` (4)
  characters with the core of the read one (multiset overlap:
  253ZOU↔532OUB = 5, 065CUA↔065CUA = 6; 132XYZ↔123ABC = 3 and
  237AAX↔853UVA = 2 do not merge, the shared region never counts), nothing
  was ever completed under that spelling (a misreading, not a neighbour), its
  entry weight is within 300 kg of the remembered tare and the exit leaves it
  at least 1000 kg heavier — the visit is renamed through
  `services.set_passage_number` (the phantom's tare memory goes, event «рейс
  был открыт под номером … — номер исправлен по памяти тары и выезду») and
  the exit closes it. Zero or several fitting visits: `_parked_entry` /
  historical tare as before. When both a phantom visit and a parked unread
  entry fit the truck, neither is taken: the exit completes from the
  historical tare and both stay for the operator.
- **Reconcile on a timer** (`reconcile_stale_visits`, called from
  `weighing_identity.process_once` at most once per
  `RECONCILE_INTERVAL_SECONDS` = 300 s, i.e. on every monitor start and then
  every five minutes): trucks that never come back leave visits open for
  good, and an open visit parks every later weighing of its plate. Under the
  lane lock every open passage (`at_silo`, entry weight set, no exit weight,
  non-empty plate) whose entry is older than 12 h ends the way a re-entry
  would end it: exactly one loaded rear weighing in `(entry, entry + 12 h)`
  that reads as this plate or that nobody read (check ended, or no check at
  all on a weighing older than the 24 h the identity worker looks back)
  closes it (event «выезд восстановлен по сроку (рейс старше 12 ч)»); no
  candidate at all, no candidate still being checked and the entry older
  than 24 h (`2 × WEIGHING_AI_ENTRY_MAX_HOURS`) cancels it with `exit_note`
  «Выезд не зафиксирован: рейс закрыт автоматически по сроку» (status event
  «рейс закрыт без выезда по сроку — выезд не был зафиксирован за 24 ч»);
  anything else (several candidates, one whose check has not ended, a visit
  between 12 h and 24 h without one) is left to the next run or the
  operator. Two limits keep the timer from guessing:
  - the window ends at the plate's own parked re-entry when there is one
    (the earliest open front weighing after the entry read as this plate or
    within two edits of it): a truck that came back and left again parks its
    re-entry as `previous_exit_missing` and its second exit as
    `earlier_entry_pending` behind it; that exit is the re-entry's, so the
    old visit is not closed with it — it waits (under 24 h) or is cancelled
    (over 24 h), after which the re-entry books the next visit and the exit
    closes that one;
  - the one candidate is taken only when it fits no other open visit (any
    `at_silo` passage with an entry weight and no exit weight that entered
    within 12 h before the weighing, is at least 1000 kg lighter, and whose
    plate the weighing reads as within two edits, or that nobody read at
    all); otherwise the visit is left. Candidates are gathered for every
    stale visit before any is settled, so a visit that had a candidate is
    never cancelled by age because this run booked that candidate elsewhere.
  The result is `{"closed", "cancelled", "left"}`; one visit's failure
  (`ValueError`, `APIException`, `IntegrityError`) is logged and the others
  are still processed.

The collector (`weighbridge/collector.py`) no longer folds every Camera-PC
refusal into `recognition_unavailable`: the answer's `status` becomes
`recognition_error` (`no_match`, `camera_unavailable`, …) and a bounded
`recognition_diagnostics` (counters, `votes`, `last_reads`, no frames) travels
with the event. The importer stores it in `AutomaticPassageCapture.ai_payload_json`
and writes a readable `error_detail` («Камера не нашла табличку: 0 из 19
кадров», «Номер не подтверждён: 2 голоса за 402BJG13 (нужно 3)»). A finished
`no_match` whose whole tally names one plate with two or more votes is kept as
a *weak plate* (`ai_payload_json.weak_plate`): a rear weak plate of a truck on
site leaving plausibly heavier books without a model call (event payload
`weak_plate: true`), a front weak plate is always checked on the frame. The
collector part reaches production only through
`activate-weighbridge.yml` with `upgrade=true` on an empty scale.
