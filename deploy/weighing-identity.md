# OpenAI weighbridge verification (asyl-ltd backend)

The wrapper is `backend/apps/grain/weighing_identity.py`. No OpenAI key or
OpenAI runtime is required on the Windows scale/camera computer. The existing
`passage-scale-monitor` runs verification in a separate bounded background
worker after the physical weight and UUID-bound photograph have been saved.
It does not call OpenAI on each scale poll. CV OCR and scale polling continue
independently; this integration does not require changing bag-counter-cv-service.

## Decision rules

With the key configured, automatic rear/unknown captures are first saved as
unassigned weighings, including exact OCR matches. Vision independently reads
the physical plates in departure and candidate entry images and compares the
individual truck/body/trailer. Database plate answers are not supplied to the
model. Generic make/colour or matching plates alone are insufficient.

Readings drop whitespace/hyphens and a leading country label `KZ` only when
the remaining complete number has a valid Kazakhstan plate format. This is
format normalization; digits/letters are never substituted to fit an answer.

Code can complete an exit only when there is exactly one clear plate match,
the image directions are front/rear, distinctive appearance agrees, and the
original exit OCR is empty, identical or differs by one character. Unclear
plates, incomparable front/rear evidence, competing matches and changed evidence
require manual verification in the existing unassigned queue.

Candidates are actual scale readings from open visits on the same scale/camera,
within 12 hours before departure, after the configured minimum trip interval.
Unassigned front weighings can recover a missed entry using their original
weight, time and photograph. Completed trips and historical tare averages are
never used. A later completed visit with the same plate disqualifies an orphan
entry. Known unrelated plate numbers are excluded before the API request; at
most six candidate photos are sent. Large ambiguous candidate sets stay manual.

The loaded weight must exceed the saved entry weight. Database locks, leases,
fresh evidence checks and existing unique active-visit constraints prevent
duplicate assignment and protect operator changes during API calls. Model
output cannot set weights or execute tools. Audit records retain the model,
response ID, original OCR, readings, comparison and actual source measurements.

No vision model can guarantee 100% identity accuracy. When the evidence is
uncertain, the system keeps weight/photo and requires operator confirmation.
Validation on screenshots does not replace a live complete entry/exit trial.

## Deployment and limits

GitHub Actions reads repository secret `OPENAI_API_KEY`, streams it through SSH
stdin and atomically writes only that variable to the server `.env` with mode
0600. It never prints the value or adds it to Git. Empty/absent Actions secrets
preserve the existing server configuration. Migration `grain.0016` creates
the durable verification queue.

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPENAI_API_KEY` | empty | Backend-only credential; no key disables verification. |
| `WEIGHING_AI_ENABLED` | `1` | Enable saved-evidence verification when a key exists. |
| `WEIGHING_AI_MODEL` | `gpt-5-mini` | Vision model, Responses API, strict structured output, low reasoning. |
| `WEIGHING_AI_MAX_DAILY_REQUESTS` | `200` | Conservative daily attempt cap; includes retries and checks waiting for evidence. |
| `WEIGHING_AI_ENTRY_MAX_HOURS` | `12` | Maximum age of an actual open entry, configurable from 1–24 hours. |

Requests use `store: false`, a 45-second socket timeout, a three-minute lease,
at most three attempts per weighing and a 5,000-output-token bound. Image size
and response size are bounded. API/storage failures and delayed entry photos
retry after 60/120 seconds, then remain for manual review. Restarting a worker
recovers expired leases. An exhausted daily budget leaves weights pending
until the next UTC day, with an explicit limit message and manual assignment.
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

The separate shipping/conveyor automation in the same release requires its
compatible CV service update and body detector; see `deploy/shipping-transports.md`.
This requirement does not apply to the OpenAI weighbridge wrapper.

## Production acceptance diagnostic

Run the **Verify production weighbridge** Actions workflow manually on `main`.
It checks saved-weight coverage and performs at most three vision requests on
existing entry/exit photographs. SQL is read-only; no trip is assigned and no
fresh camera photograph is requested. Only bounded text diagnostics are retained
in a private Actions artifact for three days, without image bytes or credentials.
Older bookings are traced by their exact photograph request UUID, never by a
similar plate, weight or time. The diagnostic fails on missing coverage, stalled
processing, a non-ready scale, API errors or disagreement in a sampled pair.

`HTTP 200` and `connected=true` alone do not establish current weight freshness.
The bridge must continuously update `updated_at`/`age_seconds` on actual sensor
messages, including repeated zero values. If it stops, verify the Windows reader
and indicator/serial connection before a new truck trial. Do not relax the
freshness threshold or relabel an old reading as fresh.
