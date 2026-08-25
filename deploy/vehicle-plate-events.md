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
