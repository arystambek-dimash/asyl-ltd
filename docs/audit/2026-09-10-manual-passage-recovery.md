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
request is made by this fallback. Manually entered tare retains its manual source
and actor and can be reused by the same plate; it is never represented as a
camera/scale measurement. The open trip can also be closed automatically by an
exact plate match on a later exit.

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
- Follow-up verification: 121 focused backend tests and 64 frontend tests passed,
  including manual tare reuse, chronology, original source display and concurrent
  plate changes. Type checking, affected ESLint checks and `git diff --check` passed.

Wagon shipping-number recognition is unchanged: production uses `gpt-6-astra`
with original-resolution images. This release does not change counting models.

## Tare persistence investigation

Read-only production audit (GitHub Actions run 34477846521) confirmed that
676VEA13 already has tare memory pointing to record 198: 3680 kg measured on
10 September at 10:07:50 +05, persisted at 10:07:57. Its completed trip 119 has
7860 kg exit and 4180 kg net. A candidate search attached to an earlier exit
at 09:59 cannot offer this later entry; this is chronological validation.

The audit also found a real omission: 123SMA13 record 182 (4680 kg) and 201DFA13
record 181 (5380 kg) were scale measurements explicitly assigned as entries by an
operator, but the original camera orientation was empty. The previous strict
`front` filter excluded them from tare memory despite their confirmed entry role.
The follow-up includes confirmed operator entries without changing the original
orientation or source, and retains the latest entry before each departure.
Migration 0019 restores eligible tare pointers and audits their before/after state;
it does not change the source weighing, photo, vehicle number or recorded weight.
Its 14 regression cases also cover stale pointers, renamed plates and a concurrent
newer entry. The combined migration/confirmed-source suite passed all 38 tests.
The already deployed manual-recovery release was checked read-only: the new
permission is present, and trip 119 still has 3680/7860/4180 kg and both photos.
