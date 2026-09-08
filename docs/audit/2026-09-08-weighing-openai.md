# Saved weighbridge identity verification

Implemented in the asyl-ltd backend; the camera-PC/CV repository is maintained
separately by the owner. Deployment/configuration and decision boundaries are
documented in `deploy/weighing-identity.md`.

Validation:

- Grain, camera, shipment and configuration regression suite: 1,678 passed
  before the additional orphan-entry regression; grain suite rerun after it.
- Frontend formatting, lint, TypeScript and all 596 tests passed; production
  build passed. Browser checked the review status and manual assignment form
  on isolated API fixtures; no browser runtime errors.
- Deploy/backup/secret synchronization: 41 tests passed, shell syntax checked,
  Django migration state checked without changes.
- Real OpenAI Responses requests with `gpt-5-mini` read rear plates `065BBE13`
  and `084ABC13` from the supplied screenshots. The actual wrapper/schema read
  entry `996BKC13` and exit `065BBE13`, identified different trucks and rejected
  that pair. No live business records were mutated by these API tests.
- Unit regressions cover one-character OCR correction, uncertain appearance,
  duplicate matches, changed evidence during the request, manual resolution,
  actual saved-entry recovery, delayed entry photos, stale/completed entries,
  daily attempts, leases and API outages. These tests mock perception.

A successful same-truck physical entry/loading/exit cycle remains a site
acceptance check. Neither screenshots nor unit tests prove 100% recognition.
All uncertain cases retain the original weight and evidence for manual review.

The GitHub secret is configured separately from source. Deployment streams it
over SSH stdin and writes the server environment without echoing it. Production
health checks establish deployment/process health, not camera recognition quality.

## Follow-up verification on saved production photographs

- Actual entry/exit pairs `411BBF13`, `854ANB13` and `X315FPM` passed independent
  plate reading and distinctive-appearance checks with `gpt-5-mini`. Initial
  responses exposed a country-label/spacing mismatch, fixed by strict format
  normalization. These references come from saved records, not independent
  human ground truth. Probes did not change business records.
- Read-only 24-hour audit at 13:54 UTC found 41 saved captures, all represented
  by a visit or unassigned weighing, with photographs. Five older captures used
  UUID photo links instead of direct capture foreign keys. Seven weighings
  remained in manual review; no stable weights were absent or stuck processing.
- The scale API was reachable, but its last reading was at 16:46:29 +05:00,
  7,717 seconds old. The five-second freshness guard correctly prevented capture.
  A successful deployment cannot resolve an inactive external sensor reader.
- Local follow-up checks: 589 grain tests; 598 frontend tests, formatting,
  lint and TypeScript. The recovery regression verifies all 30 saved weights
  enter the durable manual queue after a batch of camera failures.
