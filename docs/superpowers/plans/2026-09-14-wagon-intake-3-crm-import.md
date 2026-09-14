# Wagon Intake · Part 3 — CRM import: stops → intake trips

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The CRM imports wagon stops from the wagon collector's outbox (Part 2) and turns each stop into one simple-flow intake trip: arrival by wagon number, entry (full) weight with the cam8 frame, default-route silo, exit (empty) weight and automatic completion — while parking anything it cannot apply for the operator with a clear reason.

**Architecture:** A durable `WagonArchStop` row per imported stop (idempotent by the collector's UUID), a new importer module `apps/grain/wagon_arch.py` run from the existing `monitor_passage_scale` tick, and trip logic built only on existing services (`_arrive_expected_wagon`, `record_simple_entry_weight`, `record_simple_exit_weight`, `suggest_silos`). Departures are applied with a grace period so a re-positioned wagon (same number, weight not risen) continues its trip instead of closing it. Two read endpoints feed the UI (Part 4).

**Tech Stack:** Django 5 / DRF, PostgreSQL, pytest-django.

**Spec:** `docs/superpowers/specs/2026-09-12-wagon-intake-arch-design.md` (sections «Рейс на стоянку», «Правила и крайние случаи», «3. Импорт в CRM»).

**Repository:** `/Users/dimash/PycharmProjects/asyl-ltd`. Tests: `cd backend && .venv/bin/pytest <path> -q -p no:cacheprovider`.

## Global Constraints

- Importer directory: env `WEIGHBRIDGE_WAGON_OUTBOX_DIR` (default `/var/lib/weighbridge-wagon`); enabled only when `settings.WAGON_ARCH_AUTOMATION_ENABLED` is true **and** the directory exists. Never touches the truck outbox.
- Event contract (from Part 2): `kind ∈ {wagon_stop, wagon_departure}`, `version == 2`; other bodies are acknowledged with a logged warning (never block the queue).
- New settings: `WAGON_ARCH_EXIT_GRACE_SECONDS` (600, 60–3600) — how long a departure waits before its exit weight is applied when no next stop arrives; continuation uses `WAGON_ARCH_NEXT_WAGON_RISE_KG` (Part 2).
- Every trip mutation runs in its own `transaction.atomic()` block; a `ValidationError` (`silo_required`, `bad_tare`, `invalid_wagon_transition`, …) rolls that block back and is recorded on the stop as `blocked_reason` — the stop is retried on later ticks until the operator resolves it (e.g. assigns a silo) or it applies.
- Automatic actions use `user=None` (as `register_detected_arrival` does); log lines go through `services._log` so they appear in the trip history with `auto=True`.
- `poll_wagon_plate()` in `monitor_cameras` is skipped when `WAGON_ARCH_AUTOMATION_ENABLED` is true — the arch stops are the arrival sensor now.

---

## File structure

| File | Responsibility |
|---|---|
| `backend/apps/grain/models.py` | `WagonArchStop` model |
| `backend/apps/grain/migrations/0020_wagon_arch_stop.py` | migration |
| `backend/apps/grain/wagon_arch.py` | importer (`enabled`, `import_event`, `poll_once`, `apply_pending`), trip logic (`open_trip`, `apply_entry`, `apply_departure`, `assign_default_silo`), runtime payload |
| `backend/apps/grain/services.py` | `default_route_silo(wagon)` extracted from `suggest_silos` |
| `backend/apps/grain/views.py`, `urls.py`, `serializers.py` | `GET /grain/wagon-arch/runtime/`, `GET /grain/wagon-arch/stops/` |
| `backend/apps/grain/management/commands/monitor_passage_scale.py` | run `wagon_arch.poll_once` per tick |
| `backend/apps/cameras/management/commands/monitor_cameras.py` | skip `poll_wagon_plate` when the arch automation is on |
| `backend/config/_settings/base.py` | `WAGON_ARCH_EXIT_GRACE_SECONDS` |
| `backend/apps/grain/tests/test_wagon_arch.py` | all tests of this part |

---

### Task 1: `WagonArchStop` model and migration

**Files:**
- Modify: `backend/apps/grain/models.py` (append after `WeighingPhotoDelivery`)
- Create: `backend/apps/grain/migrations/0020_wagon_arch_stop.py` (generate with `makemigrations`)
- Test: `backend/apps/grain/tests/test_wagon_arch.py` (new)

**Interfaces:**
- Produces:

```python
class WagonArchStop(models.Model):
    OPEN, CLOSED, ATTENTION, SUPERSEDED = "open", "closed", "attention", "superseded"
    stop_id: UUID unique            # collector arrival event id
    camera: str
    arrived_at: datetime            # collector stable_weight_at
    full_weight_kg: int
    scale_age_seconds: Decimal|None; scale_updated_at: str; still_seconds: Decimal|None
    number: str; number_source: str ("model"|""); recognition_error: str; ocr_attempts: int
    photo_request_id: UUID          # == stop_id; the frame lives in WeighingPhotoDelivery(request_id)
    wagon: FK Wagon | None (SET_NULL, related_name="arch_stops")
    entry_applied_at: datetime|None
    departure_id: UUID|None (unique); exit_weight_kg: int|None; exit_stable_at: datetime|None
    departed_at: datetime|None; motion_gap: bool
    exit_applied_at: datetime|None
    status: open|closed|attention|superseded
    blocked_reason: str             # ""|silo_required|unplanned_wagon|number_missing|exit_not_lower|no_exit_weight|invalid_wagon_transition|...
    blocked_detail: str
    created_at, updated_at
```

- [ ] **Step 1: Write the failing model test**

Create `backend/apps/grain/tests/test_wagon_arch.py`:

```python
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.grain import statuses as st
from apps.grain.models import GrainSupply, Silo, SiloType, Wagon, WagonArchStop

pytestmark = pytest.mark.django_db

JPEG = b"\xff\xd8\xff\xe0" + b"1" * 64


def test_wagon_arch_stop_is_unique_per_collector_event():
    now = timezone.now()
    stop = WagonArchStop.objects.create(
        stop_id=uuid.UUID("11111111-1111-1111-1111-111111111111"), camera="cam8",
        arrived_at=now, full_weight_kg=62340, photo_request_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
    )
    assert stop.status == WagonArchStop.OPEN and stop.wagon_id is None
    with pytest.raises(Exception):
        WagonArchStop.objects.create(
            stop_id=stop.stop_id, camera="cam8", arrived_at=now, full_weight_kg=1,
            photo_request_id=stop.stop_id,
        )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && .venv/bin/pytest apps/grain/tests/test_wagon_arch.py -q -p no:cacheprovider`
Expected: FAIL at import: `ImportError: cannot import name 'WagonArchStop'`.

- [ ] **Step 3: Add the model and migration**

Append to `backend/apps/grain/models.py`:

```python
class WagonArchStop(models.Model):
    """One wagon stop under the unloading arch, imported from the wagon collector.

    The collector's arrival UUID is the idempotency key. The frame is stored as
    a ``WeighingPhotoDelivery`` with the same ``request_id`` and linked to the
    entry weighing through ``photo_request_id``.
    """

    OPEN, CLOSED, ATTENTION, SUPERSEDED = "open", "closed", "attention", "superseded"
    STATUSES = [OPEN, CLOSED, ATTENTION, SUPERSEDED]

    stop_id = models.UUIDField(unique=True)
    camera = models.CharField(max_length=32)
    arrived_at = models.DateTimeField(db_index=True)
    full_weight_kg = models.PositiveBigIntegerField()
    scale_age_seconds = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    scale_updated_at = models.CharField(max_length=64, blank=True, default="")
    still_seconds = models.DecimalField(max_digits=8, decimal_places=1, null=True, blank=True)
    number = models.CharField(max_length=30, blank=True, default="")
    number_source = models.CharField(max_length=12, blank=True, default="")
    recognition_error = models.CharField(max_length=64, blank=True, default="")
    ocr_attempts = models.PositiveSmallIntegerField(default=0)
    photo_request_id = models.UUIDField(db_index=True)
    wagon = models.ForeignKey(Wagon, null=True, blank=True, on_delete=models.SET_NULL, related_name="arch_stops")
    entry_applied_at = models.DateTimeField(null=True, blank=True)
    departure_id = models.UUIDField(null=True, blank=True, unique=True)
    exit_weight_kg = models.PositiveBigIntegerField(null=True, blank=True)
    exit_stable_at = models.DateTimeField(null=True, blank=True)
    departed_at = models.DateTimeField(null=True, blank=True)
    motion_gap = models.BooleanField(default=False)
    exit_applied_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, default=OPEN)
    blocked_reason = models.CharField(max_length=64, blank=True, default="")
    blocked_detail = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-arrived_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["open", "closed", "attention", "superseded"]),
                name="wagon_arch_stop_status_valid",
            ),
        ]
```

Generate the migration: `cd backend && .venv/bin/python manage.py makemigrations grain -n wagon_arch_stop` (it must create `0020_wagon_arch_stop.py`; inspect that it only adds this model).

- [ ] **Step 4: Run the test**

Run: `cd backend && .venv/bin/pytest apps/grain/tests/test_wagon_arch.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/grain/models.py backend/apps/grain/migrations/0020_wagon_arch_stop.py backend/apps/grain/tests/test_wagon_arch.py
git commit -m "feat(grain): WagonArchStop model for imported wagon stops"
```

---

### Task 2: Default-route silo service

**Files:**
- Modify: `backend/apps/grain/services.py:629-668` (`suggest_silos`) — extract `_default_route_silo_ids(wagon)`; add `default_route_silo(wagon)` and `assign_default_silo(wagon, user=None)`
- Test: `backend/apps/grain/tests/test_wagon_arch.py` (append)

**Interfaces:**
- Produces: `services.default_route_silo(wagon) -> Silo | None` (first suitable silo that is the default route of the wagon's culture/class; `None` for a wagon without a supply/grain type); `services.assign_default_silo(wagon, user=None) -> Silo | None` — for a `workflow == "simple"` wagon without `assigned_silo`, sets `assigned_silo` + `unloading_point`, creates a `SiloReservation` for `wagon.expected_weight_kg` when it is set, logs `grain_silo` with `auto=True`, returns the silo; returns the existing `assigned_silo` unchanged when present.

- [ ] **Step 1: Write the failing tests**

Append to `test_wagon_arch.py`:

```python
def _route(*, default=True, capacity=500_000):
    grain_type = SiloType.objects.create(name=f"Тип-{SiloType.objects.count() + 1}", grain_culture="пшеница", grain_class="3")
    silo = Silo.objects.create(
        name=f"Силос-{Silo.objects.count() + 1}", total_capacity_kg=capacity, silo_type=grain_type,
        grain_culture="пшеница", grain_class="3", unloading_line="линия 1",
    )
    if default:
        grain_type.default_silo = silo
        grain_type.save(update_fields=["default_silo"])
    return grain_type, silo


def _expected_wagon(number="28055531", *, with_silo=True, default=True):
    grain_type, silo = _route(default=default)
    supply = GrainSupply.objects.create(
        supplier="ТОО Колос", grain_type=grain_type, assigned_silo=silo, expected_total_kg=40_000,
        culture="пшеница", grain_class="3", status="expected",
    )
    return Wagon.objects.create(
        supply=supply, number=number, direction=Wagon.INTAKE, workflow="simple", status=st.EXPECTED,
        expected_weight_kg=40_000, assigned_silo=silo if with_silo else None,
    )


def test_default_route_silo_follows_the_grain_type_and_assigns_once():
    from apps.grain import services
    wagon = _expected_wagon(with_silo=True)
    silo = wagon.assigned_silo
    assert services.assign_default_silo(wagon) == silo          # already assigned: untouched

    wagon = _expected_wagon(number="28819852", with_silo=False)
    assigned = services.assign_default_silo(wagon)
    wagon.refresh_from_db()
    assert assigned is not None and wagon.assigned_silo_id == assigned.pk
    assert wagon.unloading_point == "линия 1"
    assert wagon.reservation.amount_kg == 40_000 and wagon.reservation.silo_id == assigned.pk

    no_route = _expected_wagon(number="28815116", with_silo=False, default=False)
    assert services.default_route_silo(no_route) is None
    assert services.assign_default_silo(no_route) is None
    no_route.refresh_from_db()
    assert no_route.assigned_silo_id is None

    bare = Wagon.objects.create(number="", direction=Wagon.INTAKE, workflow="simple", status=st.ARRIVED)
    assert services.default_route_silo(bare) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/pytest apps/grain/tests/test_wagon_arch.py -q -p no:cacheprovider -k default_route`
Expected: FAIL with `AttributeError: module 'apps.grain.services' has no attribute 'assign_default_silo'`.

- [ ] **Step 3: Implement**

In `services.py`, replace the `default_ids = set(SiloType.objects...)` block inside `suggest_silos` with a call to a new helper and add the two services right after `suggest_silos`:

```python
def _default_route_silo_ids(wagon: Wagon) -> set[int]:
    culture = wagon.supply.culture if wagon.supply else ""
    grain_class = wagon.supply.grain_class if wagon.supply else ""
    if not culture and not grain_class:
        return set()
    return set(
        SiloType.objects.filter(
            grain_culture=culture,
            grain_class=grain_class,
            default_silo__isnull=False,
        ).values_list("default_silo_id", flat=True)
    )


def default_route_silo(wagon: Wagon) -> Silo | None:
    """Силос основного маршрута ★ для типа зерна поставки, если он подходит."""
    default_ids = _default_route_silo_ids(wagon)
    for silo in suggest_silos(wagon):
        if silo.pk in default_ids:
            return silo
    return None


@transaction.atomic
def assign_default_silo(wagon: Wagon, user=None) -> Silo | None:
    """Назначить короткому приходу силос ★, если оператор ещё не выбрал свой."""
    _lock_wagon(wagon)
    if wagon.assigned_silo_id:
        return wagon.assigned_silo
    silo = default_route_silo(wagon)
    if silo is None:
        return None
    wagon.assigned_silo = silo
    wagon.unloading_point = silo.unloading_line
    wagon.save(update_fields=["assigned_silo", "unloading_point"])
    expected = int(wagon.expected_weight_kg or 0)
    if expected > 0:
        SiloReservation.objects.get_or_create(
            wagon=wagon, defaults={"silo": silo, "amount_kg": expected}
        )
    _log(
        wagon,
        "silo",
        f"Вагон {wagon.number or '#' + str(wagon.pk)}: силос «{silo.name}» назначен по основному маршруту",
        user,
        silo_id=silo.pk,
        auto=True,
    )
    return silo
```

Inside `suggest_silos`, `default_ids = _default_route_silo_ids(wagon)` replaces the inline query (behaviour identical: an empty culture/class previously matched only types with empty culture/class — keep that by removing the early `return set()` if any existing test relies on it; run `apps/grain/tests` to check).

- [ ] **Step 4: Run the grain tests**

Run: `cd backend && .venv/bin/pytest apps/grain/tests/test_wagon_arch.py apps/grain/tests/test_simple_intake.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/grain/services.py backend/apps/grain/tests/test_wagon_arch.py
git commit -m "feat(grain): default-route silo assignment for simple intake"
```

---

### Task 3: Importer and trip logic (`apps/grain/wagon_arch.py`)

**Files:**
- Create: `backend/apps/grain/wagon_arch.py`
- Modify: `backend/config/_settings/base.py` (add `WAGON_ARCH_EXIT_GRACE_SECONDS` next to the other `WAGON_ARCH_*`)
- Test: `backend/apps/grain/tests/test_wagon_arch.py` (append)

**Interfaces:**
- Consumes: `Outbox` (`weighbridge.outbox`), `outbox_importer._store_evidence`, `weighing_photos._link_photo`, services from Task 2 and existing ones.
- Produces:

```python
directory() -> Path; enabled() -> bool
import_event(event: dict) -> WagonArchStop | None      # idempotent; raises ValueError for unusable bodies
apply_pending(*, now=None) -> dict                      # retries blocked entries, applies due departures
poll_once(*, limit=10) -> dict                          # import + ack up to `limit`, then apply_pending, store runtime
runtime() -> dict                                       # cached payload for the UI
```

Trip rules implemented here:
1. **Arrival** (`kind == wagon_stop`): create `WagonArchStop` + `WeighingPhotoDelivery(request_id=stop_id, camera)` with the frame (`_store_evidence`). Then `open_trip(stop)`:
   - number known and an `EXPECTED` intake wagon has it → `_arrive_expected_wagon`;
   - number known and a wagon with it is on site → **continuation check** (rule 3) else `blocked_reason="wagon_on_site"`;
   - number known but nothing expected → `Wagon.objects.create(direction=INTAKE, workflow="simple", status=ARRIVED, number=number, number_source="camera", number_camera_source=camera, arrived_at=stop.arrived_at)`;
   - number unknown → same, `number=""` and `blocked_reason` stays empty (the operator fills the number later, like today's camera arrivals).
   Then `apply_entry(stop)`: `assign_default_silo` when needed, then `record_simple_entry_weight(wagon, full_weight_kg, None, source="scale", scale_number="wagon", scale_age_seconds=…, scale_updated_at=…, occurred_at=arrived_at, photo_request_id=stop_id, photo_camera=camera)` then `_link_photo(delivery)`; on `ValidationError` → `blocked_reason=code`, retried by `apply_pending`.
2. **Departure** (`kind == wagon_departure`): store `departure_id/exit_weight_kg/exit_stable_at/departed_at/motion_gap` on the stop (idempotent by `departure_id`); do **not** apply yet.
3. **Continuation**: when a new stop arrives with a known number equal to the number of the latest stop that has a pending (unapplied) departure, and `new.full_weight_kg <= previous.exit_weight_kg + WAGON_ARCH_NEXT_WAGON_RISE_KG` (or previous exit weight is `None`), then the previous stop becomes `SUPERSEDED` (its departure is never applied), and the new stop reuses `previous.wagon` with `entry_applied_at = previous.entry_applied_at` (no second entry). Otherwise the previous stop's departure is applied first.
4. **Applying a departure** (`apply_pending`): for stops with `departure_id` set and `exit_applied_at` null, when (a) a later stop exists, or (b) `departed_at + WAGON_ARCH_EXIT_GRACE_SECONDS <= now`: `exit_weight_kg is None` → `status=ATTENTION, blocked_reason="no_exit_weight"`; else `record_simple_exit_weight(wagon, exit_weight_kg, None, source="scale", scale_number="wagon", scale_age_seconds=None, scale_updated_at="", occurred_at=exit_stable_at)`; `bad_tare` → `ATTENTION, "exit_not_lower"`; success → `CLOSED`. A stop whose entry is still blocked keeps its departure pending (both applied when the entry unblocks).

- [ ] **Step 1: Write the failing tests**

Append to `test_wagon_arch.py`:

```python
def _arrival(*, number="28055531", weight=62340, at=None, stop_id=None, photo=JPEG, error=""):
    stop_id = stop_id or uuid.uuid4()
    at = at or timezone.now() - timedelta(minutes=5)
    return {
        "version": 2, "kind": "wagon_stop", "id": str(stop_id), "camera": "cam8", "weight_kg": weight,
        "stable_weight_at": at.isoformat(), "scale_age_seconds": "0.1", "scale_updated_at": "t1", "still_seconds": 11.0,
        "photo": photo, "photo_error": "" if photo else "snapshot_unavailable",
        "number": number, "number_source": "model" if number else "", "recognition": None,
        "recognition_error": error, "ocr_attempts": 1,
    }


def _departure(stop, *, weight=24120, at=None):
    at = at or timezone.now() - timedelta(minutes=1)
    return {
        "version": 2, "kind": "wagon_departure", "id": str(uuid.uuid4()), "stop_id": stop["id"], "camera": "cam8",
        "weight_kg": weight, "stable_weight_at": at.isoformat() if weight is not None else None,
        "scale_updated_at": None, "departed_at": at.isoformat(), "motion_gap": False,
    }


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.WAGON_ARCH_AUTOMATION_ENABLED = True
    settings.WAGON_ARCH_EXIT_GRACE_SECONDS = 600
    return tmp_path


def test_arrival_of_an_expected_wagon_records_the_entry_with_photo_and_silo(media):
    from apps.grain import wagon_arch
    from apps.grain.models import WeighingPhotoDelivery
    wagon = _expected_wagon(with_silo=False)
    event = _arrival()
    stop = wagon_arch.import_event(event)
    wagon.refresh_from_db()
    assert stop.wagon_id == wagon.pk and stop.status == WagonArchStop.OPEN and stop.blocked_reason == ""
    assert wagon.status == st.AT_SILO and wagon.gross_weight_kg == 62340
    assert wagon.assigned_silo_id is not None and wagon.number_source == "camera"
    record = wagon.weighings.get(kind="gross")
    assert record.source == "scale" and record.scale_number == "wagon"
    assert record.photo_request_id == stop.stop_id and record.photo.read() == JPEG
    assert WeighingPhotoDelivery.objects.get(request_id=stop.stop_id).status == "saved"
    assert wagon_arch.import_event(event) == stop          # idempotent
    assert WagonArchStop.objects.count() == 1


def test_departure_is_applied_after_the_grace_period_and_completes_the_trip(media):
    from apps.grain import wagon_arch
    wagon = _expected_wagon()
    arrival = _arrival()
    wagon_arch.import_event(arrival)
    wagon_arch.import_event(_departure(arrival, weight=24120, at=timezone.now() - timedelta(minutes=3)))
    stop = WagonArchStop.objects.get()
    assert stop.exit_weight_kg == 24120 and stop.exit_applied_at is None
    wagon.refresh_from_db()
    assert wagon.status == st.AT_SILO                      # grace: a re-positioning may still follow

    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=8))
    stop.refresh_from_db(); wagon.refresh_from_db()
    assert stop.status == WagonArchStop.CLOSED and stop.exit_applied_at is not None
    assert (wagon.tare_weight_kg, wagon.net_weight_kg, wagon.status) == (24120, 38220, st.COMPLETED)


def test_next_stop_applies_the_previous_departure_immediately(media):
    from apps.grain import wagon_arch
    first, second = _expected_wagon("28055531"), _expected_wagon("28819852")
    arrival = _arrival(number="28055531")
    wagon_arch.import_event(arrival)
    wagon_arch.import_event(_departure(arrival))
    wagon_arch.import_event(_arrival(number="28819852", at=timezone.now() - timedelta(seconds=30)))
    wagon_arch.apply_pending()
    first.refresh_from_db(); second.refresh_from_db()
    assert first.status == st.COMPLETED and second.status == st.AT_SILO
    assert list(WagonArchStop.objects.order_by("arrived_at").values_list("status", flat=True)) == ["closed", "open"]


def test_repositioned_wagon_continues_its_trip_instead_of_closing_it(media):
    from apps.grain import wagon_arch
    wagon = _expected_wagon("28055531")
    first = _arrival(number="28055531", weight=62340, at=timezone.now() - timedelta(minutes=20))
    wagon_arch.import_event(first)
    wagon_arch.import_event(_departure(first, weight=50100, at=timezone.now() - timedelta(minutes=10)))
    second = _arrival(number="28055531", weight=49800, at=timezone.now() - timedelta(minutes=9))
    second_stop = wagon_arch.import_event(second)
    wagon_arch.apply_pending()
    wagon.refresh_from_db()
    assert wagon.status == st.AT_SILO and wagon.gross_weight_kg == 62340       # no second entry
    assert WagonArchStop.objects.get(stop_id=first["id"]).status == WagonArchStop.SUPERSEDED
    assert second_stop.wagon_id == wagon.pk and second_stop.entry_applied_at is not None
    wagon_arch.import_event(_departure(second, weight=24120))
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    wagon.refresh_from_db()
    assert (wagon.tare_weight_kg, wagon.net_weight_kg, wagon.status) == (24120, 38220, st.COMPLETED)


def test_a_heavier_wagon_with_the_same_number_is_a_new_trip_not_a_continuation(media):
    from apps.grain import wagon_arch
    wagon = _expected_wagon("28055531")
    first = _arrival(number="28055531", weight=62340, at=timezone.now() - timedelta(minutes=20))
    wagon_arch.import_event(first)
    wagon_arch.import_event(_departure(first, weight=24120, at=timezone.now() - timedelta(minutes=10)))
    wagon_arch.import_event(_arrival(number="28055531", weight=61000, at=timezone.now() - timedelta(minutes=9)))
    wagon_arch.apply_pending()
    wagon.refresh_from_db()
    assert wagon.status == st.COMPLETED
    second_stop = WagonArchStop.objects.order_by("arrived_at").last()
    assert second_stop.wagon_id != wagon.pk and second_stop.wagon.status == st.AT_SILO
    assert second_stop.wagon.supply_id is None and second_stop.wagon.number == "28055531"


def test_stop_without_a_default_route_waits_for_the_operator_then_applies(media):
    from apps.grain import services, wagon_arch
    wagon = _expected_wagon(with_silo=False, default=False)
    arrival = _arrival()
    stop = wagon_arch.import_event(arrival)
    wagon.refresh_from_db()
    assert (stop.blocked_reason, wagon.status, wagon.gross_weight_kg) == ("silo_required", st.ARRIVED, None)
    wagon_arch.import_event(_departure(arrival))
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    stop.refresh_from_db()
    assert stop.exit_applied_at is None                    # the exit waits behind the entry

    services.assign_silo  # operator path exists; emulate the operator by direct assignment
    wagon.assigned_silo = wagon.supply.assigned_silo
    wagon.save(update_fields=["assigned_silo"])
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=12))
    stop.refresh_from_db(); wagon.refresh_from_db()
    assert (stop.status, stop.blocked_reason, wagon.status) == (WagonArchStop.CLOSED, "", st.COMPLETED)


def test_unknown_number_opens_a_bare_trip_and_a_bad_exit_needs_attention(media):
    from apps.grain import wagon_arch
    arrival = _arrival(number="", error="number_unreadable")
    stop = wagon_arch.import_event(arrival)
    assert stop.wagon.number == "" and stop.wagon.supply_id is None and stop.wagon.status == st.ARRIVED
    assert stop.blocked_reason == "silo_required"          # no supply → no default route
    grain_type, silo = _route()
    stop.wagon.assigned_silo = silo
    stop.wagon.save(update_fields=["assigned_silo"])
    wagon_arch.apply_pending()
    stop.refresh_from_db()
    assert stop.entry_applied_at is not None
    wagon_arch.import_event(_departure(arrival, weight=70000))
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    stop.refresh_from_db()
    assert (stop.status, stop.blocked_reason) == (WagonArchStop.ATTENTION, "exit_not_lower")
    assert stop.wagon.weighings.filter(kind="tare").count() == 0   # the rejected exit left no record

    other = wagon_arch.import_event(_arrival(number="", weight=60000, stop_id=uuid.uuid4()))
    wagon_arch.import_event(_departure({"id": str(other.stop_id)}, weight=None))
    wagon_arch.apply_pending(now=timezone.now() + timedelta(minutes=11))
    other.refresh_from_db()
    assert (other.status, other.blocked_reason) == (WagonArchStop.ATTENTION, "no_exit_weight")


def test_poll_once_imports_acks_and_publishes_runtime(media, monkeypatch, tmp_path):
    from django.core.cache import cache
    from apps.grain import wagon_arch
    from weighbridge.outbox import Outbox
    monkeypatch.setenv("WEIGHBRIDGE_WAGON_OUTBOX_DIR", str(tmp_path / "wagon"))
    box = Outbox(tmp_path / "wagon")
    _expected_wagon()
    arrival = _arrival(photo=None)
    box.put({k: v for k, v in arrival.items() if k != "photo"})
    box.finish(arrival["id"], "photo", photo=JPEG, updates={"photo_error": ""})
    box.finish(arrival["id"], "ocr", updates={"number": "28055531", "number_source": "model", "recognition": None, "recognition_error": "", "ocr_attempts": 1})
    box.put({"version": 9, "kind": "mystery", "id": str(uuid.uuid4())})
    box.finish(box.next()["id"], "photo") if False else None
    box.state("heartbeat", {"updated_at": 0, "status": "running", "standing": None, "motion": "still", "pending_writes": 0})
    cache.clear()
    assert wagon_arch.enabled() is True
    result = wagon_arch.poll_once()
    assert result["imported"] == 1 and box.counts()["pending"] == 1      # the unknown body is not acknowledged blindly
    runtime = wagon_arch.runtime()
    assert runtime["enabled"] is True and runtime["camera"] == "cam8"
    assert runtime["last_stop"]["number"] == "28055531" and runtime["collector"]["status"] == "running"
    settings_flag = wagon_arch.enabled
    monkeypatch.setenv("WEIGHBRIDGE_WAGON_OUTBOX_DIR", str(tmp_path / "missing"))
    assert settings_flag() is False
```

Adjust the odd line `box.finish(box.next()["id"], "photo") if False else None` — delete it; it is a placeholder for nothing. For the unknown-kind row the importer must log a warning and leave it unacknowledged **only** if `version == 2`; for any other version it acknowledges after logging (so a garbage row can never block the queue). Make the test reflect that: put the unknown row with `"version": 2` and assert `pending == 1` (blocked, needs a human), then a second run with a `"version": 9` row asserts it is acknowledged. Keep both expectations explicit in the test body.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/pytest apps/grain/tests/test_wagon_arch.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.grain.wagon_arch'` (the earlier tests still pass).

- [ ] **Step 3: Add the setting and implement the module**

`backend/config/_settings/base.py`, next to the other `WAGON_ARCH_*`:

```python
WAGON_ARCH_EXIT_GRACE_SECONDS = _bounded_int_env("WAGON_ARCH_EXIT_GRACE_SECONDS", 600, 60, 3600)
```

Create `backend/apps/grain/wagon_arch.py`:

```python
"""Import wagon stops from the wagon-scale collector into simple intake trips.

One stop = one wagon under the arch: arrival opens (or continues) a trip and
records the full weight; the departure records the empty weight after a
grace period, so a wagon that was only re-positioned keeps its trip.
"""
import logging
import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from apps.grain import services
from apps.grain import statuses as st
from apps.grain import weighing_photos
from apps.grain.models import Wagon, WagonArchStop, WeighingPhotoDelivery
from apps.grain.outbox_importer import _store_evidence
from weighbridge.outbox import Outbox

log = logging.getLogger(__name__)
RUNTIME_CACHE_KEY = "grain:wagon-arch:runtime:v1"
SCALE_KEY = "wagon"


def directory() -> Path:
    return Path(os.environ.get("WEIGHBRIDGE_WAGON_OUTBOX_DIR", "/var/lib/weighbridge-wagon"))


def enabled() -> bool:
    return bool(settings.WAGON_ARCH_AUTOMATION_ENABLED) and directory().is_dir()


def _aware(value):
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or timezone.is_naive(parsed):
        raise ValueError("wagon event timestamp must be an aware ISO datetime")
    return parsed


def _blocked(stop, exc):
    detail = exc.detail if isinstance(exc, ValidationError) else {}
    code = str(detail.get("code", "")) if isinstance(detail, dict) else ""
    message = str(detail.get("detail", "")) if isinstance(detail, dict) else str(exc)
    stop.blocked_reason = code or "invalid_wagon_transition"
    stop.blocked_detail = message[:300]
    stop.save(update_fields=["blocked_reason", "blocked_detail", "updated_at"])


def _clear_block(stop):
    if stop.blocked_reason or stop.blocked_detail:
        stop.blocked_reason = stop.blocked_detail = ""
        stop.save(update_fields=["blocked_reason", "blocked_detail", "updated_at"])


# -- arrival -----------------------------------------------------------------

def _previous_pending(stop):
    """The latest earlier stop whose departure is still unapplied."""
    return (
        WagonArchStop.objects.select_for_update()
        .filter(arrived_at__lt=stop.arrived_at, departure_id__isnull=False, exit_applied_at__isnull=True,
                status=WagonArchStop.OPEN)
        .order_by("-arrived_at", "-id")
        .first()
    )


def _continues(stop, previous):
    return bool(
        previous is not None and previous.wagon_id and stop.number and stop.number == previous.number
        and (previous.exit_weight_kg is None
             or stop.full_weight_kg <= previous.exit_weight_kg + settings.WAGON_ARCH_NEXT_WAGON_RISE_KG)
    )


def open_trip(stop):
    """Attach the stop to a wagon: expected by number, continued, or a bare intake."""
    previous = _previous_pending(stop)
    if _continues(stop, previous):
        previous.status = WagonArchStop.SUPERSEDED
        previous.save(update_fields=["status", "updated_at"])
        stop.wagon = previous.wagon
        stop.entry_applied_at = previous.entry_applied_at
        stop.save(update_fields=["wagon", "entry_applied_at", "updated_at"])
        services._log(stop.wagon, "arch", f"Вагон {stop.number} переставлен под аркой: рейс продолжается", None,
                      stop_id=str(stop.stop_id), auto=True)
        return stop
    if previous is not None:
        apply_departure(previous, force=True)
    if stop.number:
        expected = (
            Wagon.objects.select_for_update()
            .filter(direction=Wagon.INTAKE, number=stop.number, status=st.EXPECTED)
            .order_by("id").first()
        )
        if expected is not None:
            stop.wagon = services._arrive_expected_wagon(expected, None, stop.camera)
            stop.save(update_fields=["wagon", "updated_at"])
            return stop
        if Wagon.objects.filter(direction=Wagon.INTAKE, number=stop.number, status__in=st.ON_SITE_STATUSES).exists():
            raise ValidationError({"detail": f"Вагон {stop.number} уже на территории", "code": "wagon_on_site"})
    wagon = Wagon.objects.create(
        supply=None, number=stop.number, direction=Wagon.INTAKE, workflow="simple", status=st.ARRIVED,
        arrived_at=stop.arrived_at, number_source="camera", number_camera_source=stop.camera,
    )
    services._log(
        wagon, "arrival",
        f"Вагон встал под арку: полный вес {stop.full_weight_kg} кг"
        + (f", номер {stop.number}" if stop.number else ", номер не распознан — укажите его вручную"),
        None, stop_id=str(stop.stop_id), camera_source=stop.camera, auto=True,
    )
    stop.wagon = wagon
    stop.save(update_fields=["wagon", "updated_at"])
    return stop


def apply_entry(stop):
    wagon = stop.wagon
    if wagon is None or stop.entry_applied_at is not None:
        return
    if services.assign_default_silo(wagon) is None and not wagon.assigned_silo_id:
        raise ValidationError({"detail": "Для прихода не назначен силос", "code": "silo_required"})
    wagon.refresh_from_db()
    services.record_simple_entry_weight(
        wagon, stop.full_weight_kg, None,
        source="scale", scale_number=SCALE_KEY, scale_age_seconds=stop.scale_age_seconds,
        scale_updated_at=stop.scale_updated_at, occurred_at=stop.arrived_at,
        photo_request_id=stop.photo_request_id, photo_camera=stop.camera,
    )
    delivery = WeighingPhotoDelivery.objects.filter(request_id=stop.photo_request_id).first()
    if delivery is not None:
        weighing_photos._link_photo(delivery)
    stop.entry_applied_at = timezone.now()
    stop.save(update_fields=["entry_applied_at", "updated_at"])


def _import_arrival(event):
    key = UUID(event["id"])
    stop = WagonArchStop.objects.filter(stop_id=key).first()
    if stop is not None:
        return stop
    weight = event["weight_kg"]
    if type(weight) is not int or weight <= 0 or weight > settings.TRUCK_SCALE_MAX_WEIGHT_KG:
        raise ValueError("wagon stop weight out of range")
    with transaction.atomic():
        stop = WagonArchStop.objects.create(
            stop_id=key, camera=event["camera"], arrived_at=_aware(event["stable_weight_at"]),
            full_weight_kg=weight,
            scale_age_seconds=Decimal(str(event["scale_age_seconds"])) if event.get("scale_age_seconds") else None,
            scale_updated_at=event.get("scale_updated_at") or "",
            still_seconds=Decimal(str(event["still_seconds"])) if event.get("still_seconds") is not None else None,
            number=(event.get("number") or "").strip(), number_source=event.get("number_source") or "",
            recognition_error=event.get("recognition_error") or "", ocr_attempts=int(event.get("ocr_attempts") or 0),
            photo_request_id=key,
        )
        delivery, _ = WeighingPhotoDelivery.objects.get_or_create(request_id=key, defaults={"camera": stop.camera})
        _store_evidence(delivery, event)
    try:
        with transaction.atomic():
            open_trip(stop)
    except ValidationError as exc:
        _blocked(stop, exc)
        return stop
    try:
        with transaction.atomic():
            apply_entry(stop)
    except ValidationError as exc:
        _blocked(stop, exc)
        return stop
    _clear_block(stop)
    return stop


# -- departure ---------------------------------------------------------------

def _import_departure(event):
    departure_id = UUID(event["id"])
    stop = WagonArchStop.objects.filter(stop_id=UUID(event["stop_id"])).first()
    if stop is None:
        raise ValueError("departure refers to an unknown stop")
    if stop.departure_id == departure_id:
        return stop
    if stop.departure_id is not None:
        log.warning("Second departure %s for stop %s ignored", departure_id, stop.stop_id)
        return stop
    weight = event.get("weight_kg")
    stop.departure_id = departure_id
    stop.exit_weight_kg = int(weight) if isinstance(weight, int) and weight > 0 else None
    stop.exit_stable_at = _aware(event["stable_weight_at"]) if event.get("stable_weight_at") else None
    stop.departed_at = _aware(event["departed_at"])
    stop.motion_gap = bool(event.get("motion_gap"))
    stop.save(update_fields=["departure_id", "exit_weight_kg", "exit_stable_at", "departed_at", "motion_gap", "updated_at"])
    return stop


def apply_departure(stop, *, force=False, now=None):
    now = now or timezone.now()
    if stop.departure_id is None or stop.exit_applied_at is not None or stop.status != WagonArchStop.OPEN:
        return
    if stop.entry_applied_at is None:
        return  # the exit waits behind a blocked entry
    if not force and stop.departed_at + timedelta(seconds=settings.WAGON_ARCH_EXIT_GRACE_SECONDS) > now:
        return
    if stop.exit_weight_kg is None:
        stop.status, stop.blocked_reason = WagonArchStop.ATTENTION, "no_exit_weight"
        stop.blocked_detail = "Перед отъездом не было устойчивого веса"
        stop.save(update_fields=["status", "blocked_reason", "blocked_detail", "updated_at"])
        return
    try:
        with transaction.atomic():
            wagon = Wagon.objects.select_for_update(of=("self",)).get(pk=stop.wagon_id)
            services.record_simple_exit_weight(
                wagon, stop.exit_weight_kg, None,
                source="scale", scale_number=SCALE_KEY, scale_age_seconds=None, scale_updated_at="",
                occurred_at=stop.exit_stable_at or stop.departed_at,
            )
    except ValidationError as exc:
        _blocked(stop, exc)
        stop.status = WagonArchStop.ATTENTION
        stop.save(update_fields=["status", "updated_at"])
        return
    stop.exit_applied_at = now
    stop.status = WagonArchStop.CLOSED
    stop.blocked_reason = stop.blocked_detail = ""
    stop.save(update_fields=["exit_applied_at", "status", "blocked_reason", "blocked_detail", "updated_at"])


# -- orchestration -----------------------------------------------------------

def import_event(event):
    if event.get("version") != 2:
        raise ValueError("unsupported wagon event version")
    kind = event.get("kind")
    if kind == "wagon_stop":
        return _import_arrival(event)
    if kind == "wagon_departure":
        return _import_departure(event)
    raise ValueError(f"unknown wagon event kind: {kind!r}")


def apply_pending(*, now=None):
    now = now or timezone.now()
    retried = applied = 0
    for stop in WagonArchStop.objects.filter(status=WagonArchStop.OPEN, entry_applied_at__isnull=True, wagon__isnull=False).order_by("arrived_at"):
        try:
            with transaction.atomic():
                apply_entry(stop)
        except ValidationError as exc:
            _blocked(stop, exc)
            continue
        _clear_block(stop)
        retried += 1
    for stop in WagonArchStop.objects.filter(status=WagonArchStop.OPEN, entry_applied_at__isnull=True, wagon__isnull=True).order_by("arrived_at"):
        try:
            with transaction.atomic():
                open_trip(stop)
                apply_entry(stop)
        except ValidationError as exc:
            _blocked(stop, exc)
            continue
        _clear_block(stop)
        retried += 1
    for stop in WagonArchStop.objects.filter(status=WagonArchStop.OPEN, departure_id__isnull=False, exit_applied_at__isnull=True).order_by("arrived_at"):
        later_exists = WagonArchStop.objects.filter(arrived_at__gt=stop.arrived_at).exists()
        before = stop.exit_applied_at
        apply_departure(stop, force=later_exists, now=now)
        stop.refresh_from_db()
        if stop.exit_applied_at != before:
            applied += 1
    return {"entries_retried": retried, "departures_applied": applied}


def _runtime_payload(box, *, imported, blocked):
    heartbeat = box.state("heartbeat") or {}
    last = WagonArchStop.objects.select_related("wagon").order_by("-arrived_at", "-id").first()
    return {
        "enabled": True,
        "camera": settings.WAGON_ARCH_CAMERA,
        "collector": {**box.counts(), "status": heartbeat.get("status", "starting"),
                      "standing": heartbeat.get("standing"), "motion": heartbeat.get("motion"),
                      "heartbeat_at": heartbeat.get("updated_at")},
        "imported": imported, "blocked": blocked,
        "pending_stops": WagonArchStop.objects.filter(status=WagonArchStop.OPEN).exclude(blocked_reason="").count(),
        "attention_stops": WagonArchStop.objects.filter(status=WagonArchStop.ATTENTION).count(),
        "last_stop": None if last is None else {
            "id": last.pk, "stop_id": str(last.stop_id), "number": last.number, "status": last.status,
            "full_weight_kg": last.full_weight_kg, "exit_weight_kg": last.exit_weight_kg,
            "arrived_at": last.arrived_at.isoformat(), "wagon_id": last.wagon_id,
            "blocked_reason": last.blocked_reason,
        },
        "updated_at": timezone.now().isoformat(),
    }


def poll_once(*, limit=10):
    box = Outbox(directory())
    imported = blocked = 0
    for _ in range(limit):
        event = box.next()
        if event is None:
            break
        try:
            import_event(event)
        except ValueError as exc:
            if event.get("version") == 2:
                log.warning("Wagon event %s left in the queue: %s", event.get("id"), exc)
                blocked += 1
                break
            log.warning("Discarding unsupported wagon event %s: %s", event.get("id"), exc)
            box.ack(event["id"])
            continue
        box.ack(event["id"])
        imported += 1
    result = apply_pending()
    cache.set(RUNTIME_CACHE_KEY, _runtime_payload(box, imported=imported, blocked=blocked), 120)
    return {"imported": imported, "blocked": blocked, **result}


def runtime():
    payload = cache.get(RUNTIME_CACHE_KEY)
    if payload is None:
        return {"enabled": enabled(), "camera": settings.WAGON_ARCH_CAMERA, "collector": None,
                "pending_stops": 0, "attention_stops": 0, "last_stop": None, "updated_at": None}
    return payload
```

Check while implementing: `services._log(wagon, "arch", ...)` produces event type `grain_arch` — acceptable and greppable; `record_simple_entry_weight` is `@transaction.atomic` and our outer `transaction.atomic()` makes the whole apply a savepoint; `_arrive_expected_wagon` sets `arrived_at=now` — pass `stop.arrived_at` by updating the wagon after the call (`wagon.arrived_at = stop.arrived_at; wagon.save(update_fields=["arrived_at"])`) so the trip time equals the stop time.

- [ ] **Step 4: Run the tests**

Run: `cd backend && .venv/bin/pytest apps/grain/tests/test_wagon_arch.py -q -p no:cacheprovider`
Expected: PASS. Then run the neighbours: `apps/grain/tests/test_auto_arrival.py apps/grain/tests/test_simple_intake.py apps/grain/tests/test_durable_outbox.py` — PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/grain/wagon_arch.py backend/config/_settings/base.py backend/apps/grain/tests/test_wagon_arch.py
git commit -m "feat(grain): import wagon arch stops into simple intake trips"
```

---

### Task 4: Monitor tick, camera poll switch, read API

**Files:**
- Modify: `backend/apps/grain/management/commands/monitor_passage_scale.py:114-160` (add `wagon_future`)
- Modify: `backend/apps/cameras/management/commands/monitor_cameras.py:55-63`
- Modify: `backend/apps/grain/views.py` (append two views), `backend/apps/grain/serializers.py` (`WagonArchStopSerializer`), `backend/apps/grain/urls.py`
- Test: `backend/apps/grain/tests/test_wagon_arch.py` (append), `backend/apps/grain/tests/test_passage_scale_monitor_command.py` (append)

**Interfaces:**
- Produces: `GET /api/grain/wagon-arch/runtime/` (`grain.view`) → `wagon_arch.runtime()`; `GET /api/grain/wagon-arch/stops/?before=<id>` (`grain.view`) → `{"results": [...], "next_cursor": id|None}` with rows `{id, stop_id, arrived_at, full_weight_kg, exit_weight_kg, net_kg, number, number_source, recognition_error, status, blocked_reason, blocked_detail, motion_gap, departed_at, wagon_id, wagon_status, photo_url}` (50 per page, newest first; `photo_url` via `photos.photo_url("evidence", delivery)`).

- [ ] **Step 1: Write the failing tests**

Append to `test_wagon_arch.py`:

```python
def test_runtime_and_stops_api_require_grain_view(media, auth_client, user_with_perms):
    from apps.grain import wagon_arch
    from apps.grain.models import WeighingPhotoDelivery
    viewer = user_with_perms("arch-viewer", codes=["grain.view"])
    denied = user_with_perms("arch-denied", codes=[])
    _expected_wagon()
    stop = wagon_arch.import_event(_arrival())
    assert auth_client(denied).get("/api/grain/wagon-arch/stops/").status_code == 403
    page = auth_client(viewer).get("/api/grain/wagon-arch/stops/")
    assert page.status_code == 200
    [row] = page.data["results"]
    assert (row["number"], row["full_weight_kg"], row["status"], row["wagon_status"]) == ("28055531", 62340, "open", st.AT_SILO)
    assert row["photo_url"].startswith("/api/grain/photos/evidence/")
    assert auth_client(viewer).get("/api/grain/wagon-arch/stops/", {"before": row["id"]}).data["results"] == []
    assert auth_client(viewer).get("/api/grain/wagon-arch/stops/", {"before": "x"}).status_code == 400
    runtime = auth_client(viewer).get("/api/grain/wagon-arch/runtime/")
    assert runtime.status_code == 200 and runtime.data["enabled"] is True and runtime.data["camera"] == "cam8"


def test_camera_plate_poll_is_off_while_the_arch_automation_runs(settings):
    from unittest.mock import patch
    from apps.cameras import continuous
    from apps.cameras.management.commands.monitor_cameras import Command
    settings.WAGON_ARCH_AUTOMATION_ENABLED = True
    with patch.object(continuous, "poll_wagon_plate") as poll:
        assert Command.wagon_plate_poll_enabled() is False
    poll.assert_not_called()
```

Append to `backend/apps/grain/tests/test_passage_scale_monitor_command.py` (mirror how that file drives one tick with `--once`; find its existing test that asserts `outbox_importer.poll_once` is used in collector mode and add):

```python
def test_once_tick_imports_wagon_stops_when_enabled(settings, monkeypatch, tmp_path):
    from unittest.mock import patch
    from apps.grain import wagon_arch
    settings.WAGON_ARCH_AUTOMATION_ENABLED = True
    (tmp_path / "wagon").mkdir()
    monkeypatch.setenv("WEIGHBRIDGE_WAGON_OUTBOX_DIR", str(tmp_path / "wagon"))
    with patch.object(wagon_arch, "poll_once", return_value={"imported": 0}) as poll, \
            patch("apps.grain.outbox_importer.enabled", return_value=False), \
            patch("apps.grain.passage_scale_automation.monitor_once", return_value=type("R", (), {"state": "idle"})()):
        call_command("monitor_passage_scale", "--once")
    poll.assert_called_once()
```

(Use the same `call_command` import and heartbeat-file settings as the neighbouring tests in that module; if they set `settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE` to a tmp path, do the same.)

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/pytest apps/grain/tests/test_wagon_arch.py apps/grain/tests/test_passage_scale_monitor_command.py -q -p no:cacheprovider -k "api or plate_poll or wagon_stops"`
Expected: FAIL (404 for the new URLs; `AttributeError: type object 'Command' has no attribute 'wagon_plate_poll_enabled'`; `poll` not called).

- [ ] **Step 3: Implement**

`monitor_passage_scale.py`: add `wagon_future = None` next to the other futures; in the `once` branch, after computing `result`, add `if wagon_arch.enabled(): wagon_arch.poll_once()`; in the loop branch, after the identity submission, add:

```python
                        if wagon_future is None or wagon_future.done():
                            if wagon_future is not None:
                                finished.append(wagon_future)
                            wagon_future = (
                                pool.submit(_background_call, wagon_arch.poll_once)
                                if wagon_arch.enabled()
                                else None
                            )
```

(and include `wagon_future` in the `finished` handling so its exceptions are logged the same way). Import `from apps.grain import wagon_arch`.

`monitor_cameras.py`: add a static method on `Command`:

```python
    @staticmethod
    def wagon_plate_poll_enabled() -> bool:
        # The arch stops are the arrival sensor once the wagon collector runs.
        return not bool(settings.WAGON_ARCH_AUTOMATION_ENABLED)
```

and guard the existing block: `if self.wagon_plate_poll_enabled(): plate = continuous.poll_wagon_plate() ...`.

`serializers.py`:

```python
class WagonArchStopSerializer(serializers.ModelSerializer):
    net_kg = serializers.SerializerMethodField()
    wagon_status = serializers.CharField(source="wagon.status", default="", read_only=True)
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = WagonArchStop
        fields = ["id", "stop_id", "camera", "arrived_at", "full_weight_kg", "exit_weight_kg", "net_kg", "number",
                  "number_source", "recognition_error", "ocr_attempts", "status", "blocked_reason", "blocked_detail",
                  "motion_gap", "departed_at", "entry_applied_at", "exit_applied_at", "wagon_id", "wagon_status", "photo_url"]

    def get_net_kg(self, stop):
        return stop.full_weight_kg - stop.exit_weight_kg if stop.exit_weight_kg is not None else None

    def get_photo_url(self, stop):
        delivery = self.context.get("deliveries", {}).get(stop.photo_request_id)
        return photos.photo_url("evidence", delivery)
```

`views.py`:

```python
class WagonArchRuntimeView(PermAPIViewMixin, APIView):
    required_perms = {"get": "grain.view"}

    def get(self, request):
        response = Response(wagon_arch.runtime())
        response["Cache-Control"] = "no-store"
        return response


class WagonArchStopListView(PermAPIViewMixin, APIView):
    required_perms = {"get": "grain.view"}

    def get(self, request):
        rows = WagonArchStop.objects.select_related("wagon").order_by("-id")
        before = request.query_params.get("before")
        if before:
            if not before.isdigit() or int(before) <= 0:
                raise ValidationError("Некорректный номер страницы")
            rows = rows.filter(pk__lt=int(before))
        page = list(rows[:51])
        deliveries = {d.request_id: d for d in WeighingPhotoDelivery.objects.filter(
            request_id__in=[stop.photo_request_id for stop in page[:50]])}
        data = WagonArchStopSerializer(page[:50], many=True, context={"deliveries": deliveries}).data
        response = Response({"results": data, "next_cursor": page[49].pk if len(page) > 50 else None})
        response["Cache-Control"] = "no-store"
        return response
```

`urls.py`: `path("grain/wagon-arch/runtime/", WagonArchRuntimeView.as_view())`, `path("grain/wagon-arch/stops/", WagonArchStopListView.as_view())` (imports accordingly).

- [ ] **Step 4: Run the tests**

Run: `cd backend && .venv/bin/pytest apps/grain/tests/test_wagon_arch.py apps/grain/tests/test_passage_scale_monitor_command.py apps/grain/tests/test_auto_arrival.py apps/cameras/tests -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Full backend run and commit**

Run: `cd backend && .venv/bin/pytest -q -p no:cacheprovider` → all green.

```bash
git add backend/apps/grain/management/commands/monitor_passage_scale.py backend/apps/cameras/management/commands/monitor_cameras.py backend/apps/grain/views.py backend/apps/grain/serializers.py backend/apps/grain/urls.py backend/apps/grain/tests/test_wagon_arch.py backend/apps/grain/tests/test_passage_scale_monitor_command.py
git commit -m "feat(grain): run the wagon arch importer from the monitor and expose runtime/stops APIs"
```

---

## Rollout

1. Deploy `main` (migration 0020 runs with the backend). `WAGON_ARCH_AUTOMATION_ENABLED` stays `0` until Parts 1–2 are live and the zone is drawn; until then the importer is inert and `poll_wagon_plate` keeps working as today.
2. Enable: add `WAGON_ARCH_AUTOMATION_ENABLED=1` to `~/asyl-ltd/.env` on the server (idempotent append), `docker compose -f docker-compose.prod.yml up -d passage-scale-monitor camera-monitor backend` (env change only — or wait for the next deploy).
3. Watch `GET /api/grain/wagon-arch/runtime/` and the trip page of the first wagons; pending stops show `blocked_reason` for the operator.

## Self-review

- Spec coverage: one trip per stop, expected/unexpected/unnumbered wagons, silo ★ or wait, exit weight with validation, re-positioning continuation, unknown/None exit weight → attention, idempotent import, `poll_wagon_plate` disabled, runtime + journal APIs. ✔
- Type consistency: event keys match Part 2 (`kind`, `id`, `stop_id`, `weight_kg`, `stable_weight_at`, `departed_at`, `motion_gap`, `number`, `number_source`, `recognition_error`, `ocr_attempts`, `photo`); `WagonArchStop` fields used by the serializer exist in the model; `wagon_arch.runtime()` keys are what Part 4 consumes. ✔
- No placeholders (the one odd test line is explicitly removed in Step 1). ✔
