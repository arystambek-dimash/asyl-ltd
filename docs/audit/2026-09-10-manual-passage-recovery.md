# Audited manual outbound weighing recovery

## Behavior

Staff with `grain.correct_weighing` (and administrators) can record a missed
entry without a photograph. The command requires a full plate, cargo, whole
positive weight, actual past arrival time and a 5–300 character reason.
The normal `grain.weigh` permission does not grant correction privileges.

`POST /api/grain/passages/manual-entry/` creates an open passage with a manual
entry weighing. An optional `unassigned_weighing` identifies an existing saved
exit: that observation completes the same trip atomically, preserving the scale
weight, photo and physical time. The entered plate must agree with any plate
already on that observation. Duplicate/open entries, inverted dates, front-facing
observations and non-positive net weights are rejected. No camera or live scale
request is made by this fallback. Manually entered tare is not represented as a
physical measurement or inserted into measured tare memory; the open trip can
still be closed automatically by an exact plate match on a later exit.

`POST /api/grain/passages/{id}/correct-exit-weight/` accepts `exit_weight_kg`,
required nullable `expected_exit_weight_kg`, and `reason`. It completes a loaded
passage without an exit weight, or corrects a completed passage. Existing scale
records and photos remain unchanged; a manual record is appended with the old
weight. Net weight is recalculated and an existing physical exit time is retained.
An outdated expected weight is rejected. Intake/silo operations are outside this
command. EventLog records actor, reason, before/after values and the record ID.

All paths share the lane → observation → trip lock order, including the legacy
new-trip-from-unassigned operation. Competing requests cannot create two recoveries.

## Interface

- «Заезд вручную» in outbound page actions.
- «Указать начальный вес» on unresolved exit observations, including missing photos.
- «Изменить выездной вес» in completed outbound trip details, and explicit manual
  exit entry while loading.
- «Журнал взвешиваний» is an outbound tab; data loads only while selected.
- «Журнал машин» is removed from the management sidebar; existing URLs remain valid.

## Verification

- Backend permission, input validation, audit, photo/time preservation, automatic
  closure of manual entries, recovery idempotence and concurrent PostgreSQL tests.
- Frontend form tests cover permission checks, required date/reason, saved-exit
  payload, optimistic conflict handling and invalid weights. Navigation/sidebar
  tests cover journal lazy loading and the new placement.
- Browser check used an isolated local PostgreSQL database (`asyl_manual_ui`),
  synthetic data and a local test administrator. Recovery produced a completed
  trip (4000 kg entry / 8640 kg exit / 4640 kg net) and removed the unresolved exit.
  A subsequent UI correction to 8700 kg produced 4700 kg net, retained the 8640 kg
  scale record and physical exit timestamp, and appended an audited manual record.
- Production trip weights were not changed by this implementation or its tests.

Wagon shipping-number recognition is unchanged: production uses `gpt-6-astra`
with original-resolution images. This release does not change counting models.
