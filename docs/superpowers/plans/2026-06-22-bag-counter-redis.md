# Bag Counter (Redis live-count) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live bag counting for the loading post: the CV worker increments a Redis counter per bag (no DB writes), the «Счётчик мешков» screen shows the live number, and when the operator enters a truck number and closes the session, the total is written once to the DB via `record_loading` (order → `loading`) and the Redis counter resets.

**Architecture:** A Redis service holds `count:camera:<pk>` incremented by the webhook in a new `increment` mode (zero DB writes). A thin `counter_store` wraps Redis. A `close` endpoint reads the total, runs the existing `record_loading`, persists a `CountSession` row (history), and clears Redis. The frontend adds a counter screen (poll the live count) with the KZ plate input + close button. A CV adapter posts `+1` per bag event.

**Tech Stack:** Django 5, DRF, PostgreSQL, Redis (redis-py), pytest (fakeredis); Next.js 15 + Tailwind.

## Global Constraints

- Live count lives ONLY in Redis (`INCR`), never the DB. DB is written once at session close.
- Redis service in compose with `--appendonly yes` + named volume `redisdata` (count survives container restart).
- `REDIS_URL` env, default `redis://redis:6379/0` (compose) / `redis://localhost:6379/0` (local).
- Key format `count:camera:{camera_pk}`. No TTL (explicit close).
- Webhook `counter` camera: body with `increment` (or `bags`) → Redis INCR, order NOT touched; body WITHOUT `increment` keeps the prior final-load behaviour (record_loading).
- Close is transactional: CountSession + record_loading atomic; on business error → 400, Redis NOT reset.
- Redis unavailable → 503 with a clear message; server must not crash.
- RBAC: `cameras.view` for live count/read, `cameras.manage` for close.
- Error shape `{"detail","code"}`, Russian messages. Reuse `record_loading` (no duplicated load logic).
- Tests first (TDD). Backend from `backend/` with venv. Use `fakeredis` in tests (no live Redis needed).

---

## File Structure

```
backend/
  config/settings.py            # +REDIS_URL
  webhooks/
    counter_store.py            # increment/get/reset over Redis (redis-py, url from settings)
    models.py                   # +CountSession
    views.py                    # webhook increment mode; CountView (GET), CountCloseView (POST close)
    serializers.py              # CountSessionSerializer
    urls.py                     # /count/<pk>/, /count/<pk>/close/, count-sessions
    tests/test_counter_store.py
    tests/test_counter_api.py
  requirements.txt              # +redis, +fakeredis (dev)
docker-compose.yml              # +redis service + volume; backend depends_on + REDIS_URL
frontend/
  src/lib/types.ts              # CountSession type
  src/app/management/counter/page.tsx   # live counter + plate + close
  src/components/layout/sidebar.tsx     # +Счётчик мешков nav
integrations/
  bag_counter_client.py         # CV worker -> +1 adapter
  README.md                     # +counter usage
```

---

### Task 1: Redis service + counter_store

**Files:**
- Modify: `backend/requirements.txt`, `backend/config/settings.py`, `docker-compose.yml`
- Create: `backend/webhooks/counter_store.py`, `backend/webhooks/tests/test_counter_store.py`

**Interfaces:**
- Produces:
  - `webhooks.counter_store.increment(camera_pk: int, by: int = 1) -> int`
  - `webhooks.counter_store.get(camera_pk: int) -> int`
  - `webhooks.counter_store.reset(camera_pk: int) -> None`
  - `webhooks.counter_store.get_client()` — returns a redis client from `settings.REDIS_URL` (lazy module-level singleton).
  - Raises `counter_store.CounterUnavailable` (subclass of RuntimeError) if Redis is unreachable.

- [ ] **Step 1: Add deps + setting**

Append to `backend/requirements.txt`:
```
redis>=5.0
fakeredis>=2.21
```
Install:
```bash
cd backend && . .venv/bin/activate
pip install "redis>=5.0" "fakeredis>=2.21"
```
In `backend/config/settings.py`, after the CAMERA_ENROLL_KEY line add:
```python
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
```

- [ ] **Step 2: Write the failing test (fakeredis)**

`backend/webhooks/tests/test_counter_store.py`:
```python
import pytest
import fakeredis
from webhooks import counter_store


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(counter_store, "_client", fake)
    return fake


def test_increment_and_get():
    assert counter_store.get(7) == 0
    assert counter_store.increment(7) == 1
    assert counter_store.increment(7, by=3) == 4
    assert counter_store.get(7) == 4


def test_reset():
    counter_store.increment(7, by=5)
    counter_store.reset(7)
    assert counter_store.get(7) == 0
```

- [ ] **Step 3: Run to verify fail**

Run: `pytest webhooks/tests/test_counter_store.py -v`
Expected: FAIL (module missing).

- [ ] **Step 4: Implement counter_store**

`backend/webhooks/counter_store.py`:
```python
import redis
from django.conf import settings

_client = None


class CounterUnavailable(RuntimeError):
    pass


def get_client():
    global _client
    if _client is None:
        _client = redis.from_url(settings.REDIS_URL)
    return _client


def _key(camera_pk: int) -> str:
    return f"count:camera:{camera_pk}"


def increment(camera_pk: int, by: int = 1) -> int:
    try:
        return int(get_client().incrby(_key(camera_pk), by))
    except redis.RedisError as e:
        raise CounterUnavailable(str(e))


def get(camera_pk: int) -> int:
    try:
        v = get_client().get(_key(camera_pk))
        return int(v) if v is not None else 0
    except redis.RedisError as e:
        raise CounterUnavailable(str(e))


def reset(camera_pk: int) -> None:
    try:
        get_client().delete(_key(camera_pk))
    except redis.RedisError as e:
        raise CounterUnavailable(str(e))
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest webhooks/tests/test_counter_store.py -v`
Expected: PASS.

- [ ] **Step 6: Add Redis to compose**

In `docker-compose.yml` add a service:
```yaml
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redisdata:/data
```
Add `REDIS_URL: "redis://redis:6379/0"` to the `backend` service `environment:`,
add `redis` to backend `depends_on:`, and add `redisdata:` under top-level
`volumes:`.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(counter): Redis service + counter_store (incr/get/reset)"
```

---

### Task 2: CountSession model

**Files:**
- Modify: `backend/webhooks/models.py`, `backend/webhooks/admin.py`
- Test: covered via API in Task 4 (model is trivial; smoke-migrate here)

**Interfaces:**
- Produces: `webhooks.models.CountSession(camera FK, bags int, order FK→Order null, status, created_at, closed_at null, closed_by FK→User null)`.

- [ ] **Step 1: Add the model**

`backend/webhooks/models.py` already has `from django.conf import settings` at the
top (the WebhookCall model uses it). Append:
```python
class CountSession(models.Model):
    camera = models.ForeignKey(Camera, on_delete=models.CASCADE, related_name="count_sessions")
    bags = models.PositiveIntegerField(default=0)
    order = models.ForeignKey(
        "orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="count_sessions"
    )
    status = models.CharField(max_length=10, default="closed")
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="closed_count_sessions",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
```
If `from django.conf import settings` is not present at the top of `models.py`, add it.

- [ ] **Step 2: Register in admin**

In `backend/webhooks/admin.py`:
```python
from .models import Camera, WebhookCall, CountSession
admin.site.register([Camera, WebhookCall, CountSession])
```

- [ ] **Step 3: Migrate**

Run:
```bash
cd backend && . .venv/bin/activate
python manage.py makemigrations webhooks && python manage.py migrate
```
Expected: creates CountSession.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat(counter): CountSession model"
```

---

### Task 3: Webhook increment mode

**Files:**
- Modify: `backend/webhooks/views.py`
- Test: `backend/webhooks/tests/test_counter_api.py` (increment part)

**Interfaces:**
- Consumes: `counter_store.increment`, `Camera`.
- Produces: webhook `counter` camera with body containing `increment` (int) or `bags` while a live session is desired → increments Redis and returns the rendered template with `{{bags}}` = new count; does NOT call `record_loading`. Detection rule: if `camera.kind == "counter"` AND `"increment" in body` → live mode.

- [ ] **Step 1: Write the failing test**

`backend/webhooks/tests/test_counter_api.py`:
```python
import pytest
import fakeredis
from rest_framework.test import APIClient
from webhooks.models import Camera
from webhooks import counter_store

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr(counter_store, "_client", fakeredis.FakeRedis())


def _counter_cam():
    return Camera.objects.create(name="cnt", camera_id="counter-01", kind="counter",
                                 status="active", api_key="k", is_active=True)


def test_increment_grows_redis_not_order():
    cam = _counter_cam()
    c = APIClient()
    for _ in range(3):
        r = c.post("/api/webhook/camera/",
                   {"camera_id": "counter-01", "increment": 1},
                   format="json", HTTP_X_CAMERA_KEY="k")
        assert r.status_code == 200
    assert counter_store.get(cam.pk) == 3
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest webhooks/tests/test_counter_api.py::test_increment_grows_redis_not_order -v`
Expected: FAIL (increment not handled → tries record_loading / no order).

- [ ] **Step 3: Implement increment branch**

In `backend/webhooks/views.py` `CameraWebhookView.post`, right before the final
`return Response(process_webhook(...))`, insert:
```python
        # Режим живого счёта: counter-камера шлёт +1 на каждый мешок → Redis.
        if camera.kind == "counter" and "increment" in request.data:
            from . import counter_store
            from .templating import render_template
            try:
                by = int(request.data.get("increment") or 1)
                total = counter_store.increment(camera.pk, by)
            except counter_store.CounterUnavailable:
                return Response({"detail": "Счётчик недоступен (Redis)", "code": "counter_unavailable"}, status=503)
            ctx = {"camera_id": camera.camera_id, "decision": "allow", "allowed": True,
                   "reason": "", "order_id": None, "plate": "", "client_name": "",
                   "bags": total, "weight_kg": None, "net_weight_kg": None}
            try:
                resp = render_template(camera.response_template, ctx)
            except ValueError:
                resp = render_template("", ctx)
            return Response(resp, status=200)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest webhooks/tests/test_counter_api.py::test_increment_grows_redis_not_order -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(counter): webhook increment mode (Redis live count)"
```

---

### Task 4: Count read + close endpoints

**Files:**
- Create: in `backend/webhooks/views.py` (CountView, CountCloseView, CountSessionViewSet); `backend/webhooks/serializers.py` (CountSessionSerializer)
- Modify: `backend/webhooks/urls.py`
- Test: `backend/webhooks/tests/test_counter_api.py` (read + close)

**Interfaces:**
- Consumes: `counter_store.{get,reset}`, `record_loading`, `_find_order` (from services), `Camera`, `CountSession`.
- Produces:
  - `GET /api/count/<int:pk>/` → `{camera, camera_name, bags}` (perm `cameras.view`).
  - `POST /api/count/<int:pk>/close/` body `{plate}` → runs record_loading, writes CountSession, resets Redis; returns `{bags, order_id, status}` (perm `cameras.manage`).
  - `GET /api/count-sessions/?camera=` (perm `cameras.view`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/webhooks/tests/test_counter_api.py`:
```python
from decimal import Decimal


def _paid_arrived_order(boss, plate="123ABC02", bags_stock=100, qty=50):
    from catalog.models import Grade, Packaging, Product
    from clients.models import Client
    from orders.models import Order, OrderItem, Payment
    from warehouse.services import receive_stock
    from shipments.services import record_arrival
    g = Grade.objects.create(name="Премиум")
    pk = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    prod = Product.objects.create(grade=g, packaging=pk, price="100.00")
    receive_stock(prod, bags_stock, boss)
    cl = Client.objects.create(first_name="И", last_name="П", phone="x")
    o = Order.objects.create(client=cl, status="paid", truck_number=plate)
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    Payment.objects.create(order=o, amount=o.total_amount)
    record_arrival(o, plate, Decimal("0"), boss)  # → arrived (+shipment)
    return o


def test_get_count(auth_client, make_user):
    u = make_user(username="v")
    from rbac.models import Permission, Role
    from employees.models import Employee
    role = Role.objects.create(name="r")
    p, _ = Permission.objects.get_or_create(code="cameras.view", defaults={"section":"cameras","action":"view","label":"x"})
    role.permissions.add(p)
    Employee.objects.create(user=u, first_name="A", last_name="B", phone="x", role=role)
    cam = _counter_cam()
    counter_store.increment(cam.pk, by=12)
    r = auth_client(u).get(f"/api/count/{cam.pk}/")
    assert r.status_code == 200 and r.data["bags"] == 12


def test_close_writes_loading_and_resets(auth_client, make_user, boss):
    admin = make_user(username="root"); admin.is_superuser = True; admin.save()
    cam = _counter_cam()
    o = _paid_arrived_order(boss, plate="123ABC02")
    counter_store.increment(cam.pk, by=40)
    r = auth_client(admin).post(f"/api/count/{cam.pk}/close/", {"plate": "123 ABC 02"}, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "loading" and o.shipment.bags_loaded == 40
    assert counter_store.get(cam.pk) == 0
    from webhooks.models import CountSession
    assert CountSession.objects.filter(camera=cam, bags=40, order=o).exists()


def test_close_no_order_400_keeps_count(auth_client, make_user):
    admin = make_user(username="root2"); admin.is_superuser = True; admin.save()
    cam = _counter_cam()
    counter_store.increment(cam.pk, by=5)
    r = auth_client(admin).post(f"/api/count/{cam.pk}/close/", {"plate": "ZZZ"}, format="json")
    assert r.status_code == 400
    assert counter_store.get(cam.pk) == 5  # not reset
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest webhooks/tests/test_counter_api.py -v`
Expected: increment test passes; new ones FAIL (no endpoints).

- [ ] **Step 3: Implement serializer**

In `backend/webhooks/serializers.py` append:
```python
class CountSessionSerializer(serializers.ModelSerializer):
    camera_name = serializers.CharField(source="camera.name", read_only=True)

    class Meta:
        model = CountSession
        fields = ["id", "camera", "camera_name", "bags", "order",
                  "status", "created_at", "closed_at"]
```
And add `CountSession` to the models import at the top:
`from .models import Camera, WebhookCall, CountSession`.

- [ ] **Step 4: Implement views**

In `backend/webhooks/views.py` append (ensure `from rbac.permissions import HasPerm, PermViewSetMixin`
is at the module top — `PermViewSetMixin` is already imported; add `HasPerm`):
```python
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rbac.permissions import HasPerm
from .models import CountSession
from .serializers import CountSessionSerializer
from . import counter_store
from .services import normalize_plate, _find_order
from shipments.services import record_loading


class CountView(APIView):
    def get_permissions(self):
        return [HasPerm("cameras.view")]

    def get(self, request, pk):
        cam = Camera.objects.filter(pk=pk).first()
        if cam is None:
            return Response({"detail": "Камера не найдена", "code": "not_found"}, status=404)
        try:
            bags = counter_store.get(cam.pk)
        except counter_store.CounterUnavailable:
            return Response({"detail": "Счётчик недоступен (Redis)", "code": "counter_unavailable"}, status=503)
        return Response({"camera": cam.pk, "camera_name": cam.name, "bags": bags})


class CountCloseView(APIView):
    def get_permissions(self):
        return [HasPerm("cameras.manage")]

    def post(self, request, pk):
        cam = Camera.objects.filter(pk=pk).first()
        if cam is None:
            return Response({"detail": "Камера не найдена", "code": "not_found"}, status=404)
        plate = normalize_plate(request.data.get("plate", ""))
        try:
            bags = counter_store.get(cam.pk)
        except counter_store.CounterUnavailable:
            return Response({"detail": "Счётчик недоступен (Redis)", "code": "counter_unavailable"}, status=503)
        order = _find_order(plate)
        if order is None:
            return Response({"detail": "Заказ по номеру не найден", "code": "order_not_found"}, status=400)
        try:
            with transaction.atomic():
                record_loading(order, bags, request.user)
                CountSession.objects.create(
                    camera=cam, bags=bags, order=order, status="closed",
                    closed_at=timezone.now(), closed_by=request.user,
                )
        except ValidationError as e:
            d = e.detail
            msg = d.get("detail") if isinstance(d, dict) else str(d)
            return Response({"detail": msg, "code": "invalid"}, status=400)
        counter_store.reset(cam.pk)
        return Response({"bags": bags, "order_id": order.id, "status": "loading"})


class CountSessionViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CountSessionSerializer
    required_perms = {"list": "cameras.view"}

    def get_queryset(self):
        qs = CountSession.objects.select_related("camera", "order")
        cam = self.request.query_params.get("camera")
        return qs.filter(camera_id=cam) if cam else qs
```

- [ ] **Step 5: URLs**

In `backend/webhooks/urls.py`:
```python
from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (CameraWebhookView, CameraViewSet, WebhookCallViewSet,
                    CountView, CountCloseView, CountSessionViewSet)

router = DefaultRouter()
router.register("cameras", CameraViewSet)
router.register("webhook-calls", WebhookCallViewSet, basename="webhook-calls")
router.register("count-sessions", CountSessionViewSet, basename="count-sessions")

urlpatterns = [
    path("webhook/camera/", CameraWebhookView.as_view()),
    path("count/<int:pk>/", CountView.as_view()),
    path("count/<int:pk>/close/", CountCloseView.as_view()),
] + router.urls
```

- [ ] **Step 6: Run + full suite**

Run:
```bash
pytest webhooks/ -v
pytest -q
```
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(counter): live count GET + transactional close (record_loading) + sessions"
```

---

### Task 5: Frontend — counter screen + nav

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/components/layout/sidebar.tsx`
- Create: `frontend/src/app/management/counter/page.tsx`

**Interfaces:**
- Consumes: `/api/cameras/` (pick counter cams), `/api/count/{pk}/`, `/api/count/{pk}/close/`, `LicensePlateInput`, `can()`.
- Produces: counter screen with live polled number, plate input, close button.

- [ ] **Step 1: Add type**

In `frontend/src/lib/types.ts`:
```typescript
export interface CountSession {
  id: number; camera: number; camera_name: string; bags: number;
  order: number | null; status: string; created_at: string; closed_at: string | null;
}
```

- [ ] **Step 2: Nav item**

In `frontend/src/components/layout/sidebar.tsx`, Управление section items, after the Камеры item add:
```typescript
      { href: "/management/counter", label: "Счётчик мешков", icon: Hash, perm: "cameras.view" },
```
Add `Hash` to the lucide-react import.

- [ ] **Step 3: Counter page**

`frontend/src/app/management/counter/page.tsx`:
```tsx
"use client";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { LicensePlateInput } from "@/components/ui/license-plate-input";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import type { Camera } from "@/lib/types";

export default function CounterPage() {
  const { data: cameras } = useApi<Camera[]>("/cameras/");
  const { me } = useAuth();
  const canManage = can(me, "cameras.manage");
  const counters = (cameras ?? []).filter((c) => c.kind === "counter" && c.status === "active");

  const [camId, setCamId] = useState<number | null>(null);
  const [bags, setBags] = useState(0);
  const [plate, setPlate] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (camId == null && counters.length) setCamId(counters[0].id);
  }, [counters, camId]);

  useEffect(() => {
    if (camId == null) return;
    let alive = true;
    const tick = async () => {
      try { const { data } = await api.get(`/count/${camId}/`); if (alive) setBags(data.bags); }
      catch { /* ignore poll errors */ }
    };
    tick();
    const t = setInterval(tick, 1500);
    return () => { alive = false; clearInterval(t); };
  }, [camId]);

  async function close() {
    if (camId == null) return;
    setBusy(true); setError(""); setMsg("");
    try {
      const { data } = await api.post(`/count/${camId}/close/`, { plate });
      setMsg(`${data.bags} мешков записано в заказ #${data.order_id}`);
      setPlate(""); setBags(0);
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <AppShell title="Счётчик мешков" section="Управление"
      description="Живой счёт мешков с камеры-счётчика. Введите номер машины и завершите сессию — итог уйдёт в заказ.">
      {counters.length === 0 ? (
        <Card><CardContent className="py-10 text-center text-sm text-[var(--muted-foreground)]">
          Нет активных камер-счётчиков. Добавьте камеру типа «Счётчик загрузки».
        </CardContent></Card>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
          <Card>
            <CardHeader><CardTitle>Загружено мешков</CardTitle></CardHeader>
            <CardContent className="flex flex-col items-center gap-2 py-10">
              {counters.length > 1 && (
                <Select className="mb-4 max-w-xs" value={camId ?? ""}
                  onChange={(e) => setCamId(Number(e.target.value))}>
                  {counters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </Select>
              )}
              <div className="text-7xl font-bold tabular-nums">{bags}</div>
              <div className="text-sm text-[var(--muted-foreground)]">мешков в текущей сессии</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Завершить сессию</CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-4">
              <div className="grid gap-2">
                <Label>Номер машины</Label>
                <LicensePlateInput value={plate} onChange={setPlate} />
              </div>
              {msg && <p className="rounded-md bg-[var(--success)]/12 px-3 py-2 text-sm text-[var(--success)]">{msg}</p>}
              {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
              {canManage && (
                <Button disabled={busy || plate.replace(/\D/g, "").length < 1}
                  onClick={close}>
                  {busy ? "Сохранение…" : "Закончить сессию"}
                </Button>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </AppShell>
  );
}
```

- [ ] **Step 4: Typecheck + build**

Run:
```bash
cd frontend && npx tsc --noEmit && npm run build
```
Expected: tsc exit 0; `/management/counter` route present.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(frontend): bag-counter screen (live count + plate + close)"
```

---

### Task 6: CV adapter + verification

**Files:**
- Create: `integrations/bag_counter_client.py`
- Modify: `integrations/README.md`
- Verification (Docker).

**Interfaces:**
- Consumes: webhook `increment` mode.
- Produces: adapter posting `+1` per bag event.

- [ ] **Step 1: Write the adapter**

`integrations/bag_counter_client.py`:
```python
"""
Мост: CV-воркер (счёт мешков) -> вебхук +1 на каждый мешок.
Запуск рядом с моделью (GPU). CV-код не меняется.

    pip install requests   # opencv/ultralytics — из cv_service_handoff
"""
import requests

WEBHOOK_URL = "http://localhost:8000/api/webhook/camera/"
CAMERA_ID = "counter-01"
CAMERA_KEY = "ВСТАВЬТЕ_КЛЮЧ_КАМЕРЫ"


def push_one():
    try:
        requests.post(WEBHOOK_URL,
                      json={"camera_id": CAMERA_ID, "increment": 1},
                      headers={"X-Camera-Key": CAMERA_KEY}, timeout=2)
    except requests.RequestException as e:
        print("[counter] не удалось отправить +1:", e)


def run_with_model():
    # from bag_pipeline import BagColorCounter
    # counter = BagColorCounter(det_weights="weights/detector.pt",
    #                           cls_weights="weights/color_classifier.pt",
    #                           camera_id=CAMERA_ID, line=(0.0,0.55,1.0,0.55),
    #                           direction="positive", device="0")
    # for event in counter.run("rtsp://admin:ПАРОЛЬ@192.168.1.64:554/Streaming/Channels/101"):
    #     push_one()
    raise SystemExit("Раскомментируйте run_with_model() и подставьте RTSP/веса.")


if __name__ == "__main__":
    # тест без модели: 5 «мешков»
    for _ in range(5):
        push_one()
    print("Отправлено 5 инкрементов. Откройте экран «Счётчик мешков».")
```

- [ ] **Step 2: Append README section**

Append to `integrations/README.md`:
```markdown

## Счётчик мешков (живой счёт)

CV-воркер шлёт `+1` на каждый посчитанный мешок:
```
for event in counter.run(rtsp_url):
    requests.post("http://localhost:8000/api/webhook/camera/",
                  json={"camera_id": "counter-01", "increment": 1},
                  headers={"X-Camera-Key": "<ключ>"})
```
Счёт виден в CRM → **Управление → Счётчик мешков**. Оператор вводит номер
машины и жмёт «Закончить сессию» — итог уходит в заказ (статус `loading`).
```

- [ ] **Step 3: Verify in Docker**

```bash
cd /Users/dimash/PycharmProjects/asyl-ltd
docker compose build backend frontend && docker compose up -d
sleep 14
```
Then: create a counter camera (admin), fire 3 webhook increments → `GET /api/count/<pk>/` shows 3; create a paid+arrived order with a truck number, `POST /api/count/<pk>/close/` with that plate → 200, order `loading`, bags=3, count reset to 0. `/management/counter` page → 200.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "docs(integrations): bag-counter CV adapter + usage"
```

---

## Self-Review Notes (coverage map)

- §1 Redis service (AOF+volume, REDIS_URL) → Task 1.
- §2 counter_store incr/get/reset + CounterUnavailable → Task 1.
- §3 CountSession (DB, written at close) → Task 2.
- §4 increment webhook mode (no DB) → Task 3; GET count + transactional close (record_loading) + sessions list; 503 on Redis down; 400 keeps count → Task 4.
- §5 counter screen (live poll, plate, close) + nav → Task 5.
- §6 CV adapter (+1 per bag) → Task 6.
- §7 RBAC view/manage, transaction, reuse record_loading, tests (fakeredis) → Tasks 1,3,4.
- Out of scope (auto-detect, websocket, color) → not implemented (correct).
