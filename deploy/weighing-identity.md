# OpenAI weighbridge verification (asyl-ltd backend)

The wrapper is `backend/apps/grain/weighing_identity.py`. No OpenAI key or
OpenAI runtime is required on the Windows scale/camera computer. The existing
`passage-scale-monitor` runs verification in a separate bounded background
worker after the physical weight and UUID-bound photograph have been saved.
It does not call OpenAI on each scale poll. CV OCR and scale polling continue
independently; this integration does not require changing bag-counter-cv-service.

## Decision rules

With the key configured, every automatic capture is first saved as an
unassigned weighing with an identity check. The worker takes the oldest open
weighing of the last 24 hours and books it by an explicitly read plate and
direction (`backend/apps/grain/automatic_routing.py`): a front reading opens or
continues a visit, a rear reading closes it with the loaded weight.

OCR goes first. A valid OCR plate with a known front/rear direction is booked
without an OpenAI request. Exactly one frame, the saved photograph of this
weighing, is sent to the model only when OCR is not enough:

- one changed character would name another known truck (tare memory or an
  on-site visit);
- a rear plate is not the single on-site visit that leaves at least 1,000 kg
  heavier than its entry;
- a front plate is weak (two camera votes of three);
- OCR booking fails, for example there is no plate or no direction.

The model reads the plate and direction from the frame alone; database plate
answers, candidate photos and weights are not supplied. Its reading is booked
by the same rules. A rear reading does not overrule an OCR plate whose truck is
on site when the model's variant names nobody; the model number is kept in the
audit evidence.

Readings drop whitespace/hyphens and a leading country label `KZ` only when
the remaining complete number has a valid Kazakhstan plate format. This is
format normalization; digits/letters are never substituted to fit an answer.

An unreadable plate, an unknown direction or a weighing changed during the
request go to manual verification in the existing unassigned queue. A rear
weighing waits while an earlier front weighing of a similar plate is still
being processed. A missing entry prerequisite (`saved_tare_missing`,
`entry_weight_required`, `previous_exit_missing`) is rechecked every 30 seconds
without a paid request. A photograph still being delivered is retried every
15 seconds; an unavailable photograph leaves the weighing for review.

Visits open longer than `WEIGHING_AI_ENTRY_MAX_HOURS` are reconciled every five
minutes: the one unread loaded exit of the visit's window closes it; with no
candidate and twice that age gone the visit is cancelled without an exit;
anything ambiguous is left for the operator.

Database locks, leases, fresh evidence checks and existing unique active-visit
constraints prevent duplicate assignment and protect operator changes during
API calls. Model output cannot set weights or execute tools. Audit records
retain the identity source (`ocr`/`gpt`), model, response ID, original OCR,
reading and actual source measurements.

No vision model can guarantee 100% identity accuracy. When the evidence is
uncertain, the system keeps weight/photo and requires operator confirmation.
Validation on screenshots does not replace a live complete entry/exit trial.

## Deployment and limits

GitHub Actions reads repository secret `OPENAI_API_KEY`, streams it through SSH
stdin and atomically writes that variable to the server `.env` with mode
0600. The optional repository variables `SHIPPING_WAGON_AI_MODEL` and
`SHIPPING_WAGON_AI_DETAIL` use the same path and change shipping wagon OCR
independently of the weighbridge and truck OCR fallback. Truck fallback retains
`WEIGHING_AI_MODEL` and `high` detail. None of the values are printed or added to Git.
Empty/absent Actions values preserve
the existing server configuration; to reset a shipping override, set its server
`.env` value to empty (and remove the repository variable). Migration `grain.0016` creates
the durable verification queue.

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPENAI_API_KEY` | empty | Backend-only credential; no key disables verification. |
| `WEIGHING_AI_ENABLED` | `1` | Enable saved-evidence verification when a key exists. |
| `WEIGHING_AI_MODEL` | `gpt-5-mini` | Vision model, Responses API, strict structured output, low reasoning. |
| `SHIPPING_WAGON_AI_MODEL` | inherits `WEIGHING_AI_MODEL` | Shipping wagon OCR only; an empty value preserves the existing shared model. |
| `SHIPPING_WAGON_AI_DETAIL` | `high` | Wagon image detail: only `high` or `original`; select `original` only with a model supporting it. |
| `WEIGHING_AI_MAX_DAILY_REQUESTS` | `200` | Conservative daily attempt cap; includes retries and checks waiting for evidence. |
| `WEIGHING_AI_ENTRY_MAX_HOURS` | `12` | Maximum age of an actual open entry, configurable from 1–24 hours. |

Requests use `store: false`, a 45-second socket timeout, a three-minute lease,
at most six single-frame requests per weighing and a 1,200-output-token bound.
Image size and response size are bounded. API/storage failures retry after
60 seconds per attempt made, then remain for manual review. Restarting a worker
recovers expired leases. An exhausted daily budget leaves weights pending
until the next local (`TIME_ZONE`) day, with an explicit limit message and manual assignment.
Missing photos are shown as waiting for a photo; evidence older than 24 hours
requires operator review. No pending label implies that a model is still running.
This is an upper bound on attempts, not a fixed monetary spending guarantee.

`WEIGHING_AI_ENABLED=0` restores the previous OCR matching path for new captures;
existing unassigned weights remain available for manual assignment.

To inspect configuration without exposing the key:

```sh
docker compose -f docker-compose.prod.yml exec -T backend python manage.py shell -c \
  'from django.conf import settings; print({"enabled": settings.WEIGHING_AI_ENABLED, "key_configured": bool(settings.OPENAI_API_KEY), "model": settings.WEIGHING_AI_MODEL})'
docker compose -f docker-compose.prod.yml logs --tail=100 passage-scale-monitor
```

## Production acceptance diagnostic

Run the **Verify production weighbridge** Actions workflow manually on `main`.
It checks saved-weight coverage and performs at most three vision requests on
existing entry/exit photographs. SQL is read-only; no trip is assigned and no
fresh camera photograph is requested. Actions logs/artifacts contain only an
allowlist of boolean health signals: no plates, weights, timestamps, row IDs,
image bytes or credentials. Repository visibility does not protect these reports.
Older bookings are traced by their exact photograph request UUID, never by a
similar plate, weight or time. The diagnostic fails on missing coverage, stalled
processing, a non-ready scale, API errors or disagreement in a sampled pair.

`HTTP 200` and `connected=true` alone do not establish current weight freshness.
The bridge must continuously update `updated_at`/`age_seconds` on actual sensor
messages, including repeated zero values. If it stops, verify the Windows reader
and indicator/serial connection before a new truck trial. Do not relax the
freshness threshold or relabel an old reading as fresh.
