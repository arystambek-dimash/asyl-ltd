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
