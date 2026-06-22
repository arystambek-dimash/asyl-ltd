# Camera Webhooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let cameras drive the shipping post via a webhook: a camera posts its `camera_id` + plate (+ bags/weight), the server authenticates by API key, runs the matching shipment service (arrive/load/ship), logs the call, and replies with a per-camera JSON response template. Cameras are managed in a new UI section with key, template, a call simulator, and a call log.

**Architecture:** New `webhooks` app holds `Camera` and `WebhookCall` models, a key-authenticated `POST /api/webhook/camera/` endpoint, a service that resolves the camera by `camera_id`, verifies the key, dispatches by `kind` to the existing `record_arrival`/`record_loading`/`record_shipment` services (no duplicated logic), and renders a safe `{{var}}` JSON template. A camera CRUD API (RBAC-gated) plus a simulate endpoint power the UI. The frontend adds a Камеры list + detail (template editor, simulator, call log).

**Tech Stack:** Django 5, DRF, PostgreSQL, pytest; Next.js 15 + Tailwind + zustand.

## Global Constraints

- New app `webhooks`. Single source for permission codes stays `rbac/perms.py`; add a `cameras` section there.
- Webhook endpoint `POST /api/webhook/camera/` is NOT JWT-protected; it authenticates by `X-Camera-Key` header matched against the camera found by `camera_id` in the body. Excluded from default DRF auth/permissions (AllowAny + no auth class on that view).
- Camera identification order: `camera_id` (body) → 404 if unknown; key mismatch → 401; inactive → 403.
- `kind ∈ {entry, counter, exit}`. entry→record_arrival, counter→record_loading(bags), exit→record_shipment(weight_kg). Reuse existing services; never duplicate shipment logic.
- Business errors (no order / unpaid / wrong status) → HTTP 200 with `decision="deny"` + reason. Only key/camera problems → 401/403/404.
- Response template: safe `{{var}}` substitution (no eval). Booleans/numbers unquoted, strings escaped. Result must be valid JSON. Empty template → default `{"decision","allowed","order_id","reason"}`.
- Template variables: `camera_id, decision, allowed, reason, order_id, plate, client_name, bags, weight_kg, net_weight_kg`.
- Plate normalization: uppercase, strip non-alphanumerics, before matching `Order.truck_number` (also normalized the same way).
- `api_key` generated with `secrets.token_urlsafe`; masked in list/detail (only full on create/regenerate). `camera_id` unique.
- Webhook processing (service call + WebhookCall write + last_seen update) is one DB transaction.
- Error shape `{"detail","code"}`, Russian messages. RBAC: `cameras.view` (read/log), `cameras.manage` (CRUD/regenerate/simulate).
- Tests written first (TDD). Backend run from `backend/` with venv active.

---

## File Structure

```
backend/
  webhooks/
    perms_note.md        # (no file) — codes added to rbac/perms.py instead
    models.py            # Camera, WebhookCall
    templating.py        # render_template(template_str, context) -> str (safe {{var}})
    services.py          # process_webhook(camera, body) -> (decision, reason, order, response_dict)
    serializers.py       # CameraSerializer (masked key), WebhookCallSerializer
    views.py             # CameraWebhookView (key auth), CameraViewSet, SimulateView, regenerate action
    urls.py
    admin.py
    tests/
      test_templating.py
      test_webhook.py
      test_cameras_api.py
  rbac/perms.py          # +cameras section, +preset grants
  config/urls.py         # include webhooks.urls

frontend/
  src/lib/types.ts       # Camera, WebhookCall types
  src/app/management/cameras/page.tsx        # list + create modal
  src/app/management/cameras/[id]/page.tsx   # detail: template editor, simulator, call log
  src/components/layout/sidebar.tsx          # +Камеры under Управление
```

---

### Task 1: Permission codes for cameras

**Files:**
- Modify: `backend/rbac/perms.py`
- Test: `backend/rbac/tests/test_catalog.py` (extend)

**Interfaces:**
- Produces: codes `cameras.view`, `cameras.manage` in `ALL_CODES`; granted to the `Начальник` preset.

- [ ] **Step 1: Write failing test**

Append to `backend/rbac/tests/test_catalog.py`:
```python
def test_cameras_codes_present():
    from rbac.perms import ALL_CODES
    assert "cameras.view" in ALL_CODES
    assert "cameras.manage" in ALL_CODES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest rbac/tests/test_catalog.py::test_cameras_codes_present -v`
Expected: FAIL (codes missing).

- [ ] **Step 3: Add the section and preset grant**

In `backend/rbac/perms.py`, add to `_SECTIONS`:
```python
    "cameras": ("Камеры", ["view", "manage"]),
```
And grant to the boss preset — in `PRESETS["Начальник"]` add `"cameras"` to the `_codes(...)` arg list:
```python
    "Начальник": _codes("catalog", "clients", "orders", "payments.view",
                        "payments.create", "warehouse", "shipping", "cameras",
                        "reports.view", "events.view"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest rbac/tests/test_catalog.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(rbac): add cameras.view/manage permission codes"
```

---

### Task 2: Camera + WebhookCall models

**Files:**
- Create: `backend/webhooks/__init__.py`, `apps.py`, `models.py`, `admin.py`, `tests/__init__.py`
- Modify: `backend/config/settings.py` (add `"webhooks"`)

**Interfaces:**
- Produces:
  - `webhooks.models.Camera(name, camera_id unique, kind, api_key, response_template, is_active, last_seen)` with classmethod-free constant `KINDS = ["entry","counter","exit"]` and helper `Camera.generate_key() -> str` (staticmethod using `secrets.token_urlsafe(24)`).
  - `webhooks.models.WebhookCall(camera FK, plate, payload_bags int null, payload_weight Decimal null, matched_order FK→orders.Order null, decision, reason, request_payload JSON, response_payload JSON, created_at)`.

- [ ] **Step 1: Create app package**

Run:
```bash
cd backend && . .venv/bin/activate
mkdir -p webhooks/migrations webhooks/tests
touch webhooks/__init__.py webhooks/migrations/__init__.py webhooks/tests/__init__.py
```
Create `webhooks/apps.py`:
```python
from django.apps import AppConfig


class WebhooksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "webhooks"
```

- [ ] **Step 2: Write the models**

`backend/webhooks/models.py`:
```python
import secrets
from django.db import models


class Camera(models.Model):
    KINDS = [("entry", "Въезд"), ("counter", "Счётчик загрузки"), ("exit", "Выезд")]

    name = models.CharField(max_length=120)
    camera_id = models.CharField(max_length=60, unique=True)
    kind = models.CharField(max_length=20, choices=KINDS)
    api_key = models.CharField(max_length=80)
    response_template = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    last_seen = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def generate_key() -> str:
        return secrets.token_urlsafe(24)

    def __str__(self):
        return f"{self.camera_id} ({self.kind})"


class WebhookCall(models.Model):
    camera = models.ForeignKey(Camera, on_delete=models.CASCADE, related_name="calls")
    plate = models.CharField(max_length=30, blank=True, default="")
    payload_bags = models.IntegerField(null=True, blank=True)
    payload_weight = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    matched_order = models.ForeignKey(
        "orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="webhook_calls"
    )
    decision = models.CharField(max_length=10)  # allow / deny
    reason = models.CharField(max_length=300, blank=True, default="")
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
```

`backend/webhooks/admin.py`:
```python
from django.contrib import admin
from .models import Camera, WebhookCall

admin.site.register([Camera, WebhookCall])
```

- [ ] **Step 3: Register and migrate**

Add `"webhooks"` to `INSTALLED_APPS` in `backend/config/settings.py`.
Run:
```bash
python manage.py makemigrations webhooks && python manage.py migrate
```
Expected: creates Camera + WebhookCall.

- [ ] **Step 4: Smoke test the key generator**

`backend/webhooks/tests/test_models.py`:
```python
from webhooks.models import Camera


def test_generate_key_is_unique_and_long():
    a, b = Camera.generate_key(), Camera.generate_key()
    assert a != b
    assert len(a) >= 24
```
Run: `pytest webhooks/tests/test_models.py -v`
Expected: PASS (no DB needed).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(webhooks): Camera + WebhookCall models"
```

---

### Task 3: Safe template rendering

**Files:**
- Create: `backend/webhooks/templating.py`, `backend/webhooks/tests/test_templating.py`

**Interfaces:**
- Produces:
  - `webhooks.templating.render_template(template: str, ctx: dict) -> dict` — substitutes `{{var}}` for known keys in `ctx`, parses the result as JSON, returns a dict. Empty template → default dict. Raises `ValueError` if substituted text is not valid JSON.
  - `webhooks.templating.DEFAULT_TEMPLATE` constant string.
  - Booleans render as `true/false`, numbers bare, `None`→`null`, strings JSON-escaped (quotes kept by template author around string placeholders).

- [ ] **Step 1: Write failing tests**

`backend/webhooks/tests/test_templating.py`:
```python
import pytest
from webhooks.templating import render_template


def test_substitutes_bool_and_number_unquoted():
    out = render_template('{"open": {{allowed}}, "order": {{order_id}}}',
                          {"allowed": True, "order_id": 42})
    assert out == {"open": True, "order": 42}


def test_string_placeholder_escaped():
    out = render_template('{"msg": "{{reason}}"}', {"reason": 'нет "заказа"'})
    assert out == {"msg": 'нет "заказа"'}


def test_none_renders_null():
    out = render_template('{"order": {{order_id}}}', {"order_id": None})
    assert out == {"order": None}


def test_empty_template_uses_default():
    out = render_template("", {"decision": "allow", "allowed": True,
                               "order_id": 7, "reason": ""})
    assert out["decision"] == "allow" and out["allowed"] is True


def test_invalid_json_raises():
    with pytest.raises(ValueError):
        render_template('{"x": {{reason}}}', {"reason": "unquoted text"})
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest webhooks/tests/test_templating.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

`backend/webhooks/templating.py`:
```python
import json
import re

DEFAULT_TEMPLATE = ('{"decision": "{{decision}}", "allowed": {{allowed}}, '
                    '"order_id": {{order_id}}, "reason": "{{reason}}"}')

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _render_value(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    # strings: emit JSON-escaped WITHOUT surrounding quotes
    # (template author wraps string placeholders in quotes)
    return json.dumps(str(v))[1:-1]


def render_template(template: str, ctx: dict) -> dict:
    tpl = template.strip() or DEFAULT_TEMPLATE

    def repl(m):
        key = m.group(1)
        return _render_value(ctx.get(key))

    rendered = _PLACEHOLDER.sub(repl, tpl)
    try:
        return json.loads(rendered)
    except json.JSONDecodeError as e:
        raise ValueError(f"Шаблон даёт некорректный JSON: {e}")
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest webhooks/tests/test_templating.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(webhooks): safe {{var}} JSON response templating"
```

---

### Task 4: process_webhook service (dispatch by kind)

**Files:**
- Create: `backend/webhooks/services.py`, `backend/webhooks/tests/test_webhook.py`
- Modify: none

**Interfaces:**
- Consumes: `record_arrival(order, truck_number, weigh_in_kg, user, debt_override=False)`, `record_loading(order, bags, user)`, `record_shipment(order, weigh_out_kg, user)` from `shipments.services`; `orders.models.Order`.
- Produces:
  - `webhooks.services.normalize_plate(s: str) -> str`.
  - `webhooks.services.process_webhook(camera, body: dict) -> dict` — runs the kind-specific service in a transaction, writes a `WebhookCall`, updates `camera.last_seen`, returns the rendered response dict. `body` keys: `plate`, optional `bags`, `weight_kg`. Sets `decision`/`reason` from success/ValidationError. Never raises for business errors.

Note on weights: entry has no weigh-in from the camera in this iteration → pass `weigh_in_kg=0` to `record_arrival` (camera entry just gates; the scale weigh-in stays manual or a later field). counter passes `bags`. exit passes `weight_kg` as `weigh_out_kg`.

- [ ] **Step 1: Write failing tests**

`backend/webhooks/tests/test_webhook.py`:
```python
import pytest
from decimal import Decimal
from catalog.models import Grade, Packaging, Product
from clients.models import Client
from orders.models import Order, OrderItem, Payment
from warehouse.services import receive_stock
from webhooks.models import Camera, WebhookCall
from webhooks.services import process_webhook, normalize_plate

pytestmark = pytest.mark.django_db


def _camera(kind, tpl=""):
    return Camera.objects.create(name=kind, camera_id=f"{kind}-01", kind=kind,
                                 api_key="k", response_template=tpl)


def _paid_order(boss, status="paid", plate="123ABC02", bags_stock=100, qty=50):
    g = Grade.objects.create(name="Премиум")
    pk = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    prod = Product.objects.create(grade=g, packaging=pk, price="100.00")
    receive_stock(prod, bags_stock, boss)
    c = Client.objects.create(first_name="И", last_name="П", phone="x")
    o = Order.objects.create(client=c, status=status, truck_number=plate)
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    if status in ("paid", "arrived", "loading"):
        Payment.objects.create(order=o, amount=o.total_amount)
    return o, prod


def test_normalize_plate():
    assert normalize_plate("123 abc 02") == "123ABC02"


def test_entry_allows_paid_order(boss):
    o, _ = _paid_order(boss, status="paid")
    cam = _camera("entry", '{"open": {{allowed}}, "order": {{order_id}}}')
    resp = process_webhook(cam, {"plate": "123 ABC 02"})
    o.refresh_from_db()
    assert o.status == "arrived"
    assert resp == {"open": True, "order": o.id}
    call = WebhookCall.objects.get()
    assert call.decision == "allow" and call.matched_order_id == o.id


def test_entry_denies_unpaid(boss):
    o, _ = _paid_order(boss, status="confirmed")  # no payment
    cam = _camera("entry")
    resp = process_webhook(cam, {"plate": "123ABC02"})
    assert resp["decision"] == "deny"
    assert "оплач" in resp["reason"].lower()
    o.refresh_from_db(); assert o.status == "confirmed"


def test_entry_denies_no_order():
    cam = _camera("entry")
    resp = process_webhook(cam, {"plate": "999ZZZ99"})
    assert resp["decision"] == "deny"


def test_counter_records_bags(boss):
    o, _ = _paid_order(boss, status="arrived")
    cam = _camera("counter")
    resp = process_webhook(cam, {"plate": "123ABC02", "bags": 50})
    o.refresh_from_db()
    assert o.status == "loading" and resp["decision"] == "allow"
    assert o.shipment.bags_loaded == 50


def test_exit_records_shipment(boss):
    o, prod = _paid_order(boss, status="arrived")
    # advance to loading first via counter
    process_webhook(_camera("counter"), {"plate": "123ABC02", "bags": 50})
    o.refresh_from_db()
    # need weigh_in for net calc → set on shipment
    o.shipment.weigh_in_kg = Decimal("8000"); o.shipment.save()
    cam = _camera("exit")
    resp = process_webhook(cam, {"plate": "123ABC02", "weight_kg": 10500})
    o.refresh_from_db()
    assert o.status == "shipped" and resp["decision"] == "allow"


def test_last_seen_updated(boss):
    cam = _camera("entry")
    assert cam.last_seen is None
    process_webhook(cam, {"plate": "X"})
    cam.refresh_from_db()
    assert cam.last_seen is not None
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest webhooks/tests/test_webhook.py -v`
Expected: FAIL (services missing).

- [ ] **Step 3: Implement the service**

`backend/webhooks/services.py`:
```python
import re
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from orders.models import Order
from shipments.services import record_arrival, record_loading, record_shipment
from .models import WebhookCall
from .templating import render_template


def normalize_plate(s: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", (s or "").upper())


def _find_order(plate_norm: str):
    for o in Order.objects.select_related("client").all():
        if normalize_plate(o.truck_number) == plate_norm and plate_norm:
            return o
    return None


def _system_user(camera):
    # Webhook acts without a logged-in user; pass None where services allow it.
    return None


def process_webhook(camera, body: dict) -> dict:
    plate_raw = body.get("plate", "")
    plate = normalize_plate(plate_raw)
    bags = body.get("bags")
    weight = body.get("weight_kg")
    user = _system_user(camera)

    decision, reason, order = "deny", "", None
    with transaction.atomic():
        order = _find_order(plate)
        if order is None:
            reason = "Заказ по номеру не найден"
        else:
            try:
                if camera.kind == "entry":
                    record_arrival(order, order.truck_number or plate_raw,
                                   Decimal("0"), user)
                elif camera.kind == "counter":
                    record_loading(order, int(bags or 0), user)
                elif camera.kind == "exit":
                    record_shipment(order, Decimal(str(weight or 0)), user)
                else:
                    raise ValidationError({"detail": "Неизвестный тип камеры", "code": "bad_kind"})
                decision = "allow"
                order.refresh_from_db()
            except ValidationError as e:
                d = e.detail
                reason = d.get("detail") if isinstance(d, dict) else str(d)

        ctx = _build_context(camera, plate, order, decision, reason, bags, weight)
        try:
            response = render_template(camera.response_template, ctx)
        except ValueError:
            response = render_template("", ctx)  # fall back to default

        WebhookCall.objects.create(
            camera=camera, plate=plate, payload_bags=bags,
            payload_weight=Decimal(str(weight)) if weight is not None else None,
            matched_order=order if (order and order.pk) else None,
            decision=decision, reason=reason or "",
            request_payload=body, response_payload=response,
        )
        camera.last_seen = timezone.now()
        camera.save(update_fields=["last_seen"])
    return response


def _build_context(camera, plate, order, decision, reason, bags, weight):
    net = None
    if order is not None:
        ship = getattr(order, "shipment", None)
        net = str(ship.net_weight_kg) if ship and ship.net_weight_kg is not None else None
    return {
        "camera_id": camera.camera_id,
        "decision": decision,
        "allowed": decision == "allow",
        "reason": reason or "",
        "order_id": order.id if order else None,
        "plate": plate,
        "client_name": order.client.name if order else "",
        "bags": bags,
        "weight_kg": weight,
        "net_weight_kg": net,
    }
```

Note: `record_arrival`/`record_loading`/`record_shipment` reference `user.username`/`user.has_perm_code` only when `debt_override`/logging — passing `user=None` works for the happy paths in tests because debt override isn't triggered (orders are paid) and `log_event(user=None)` is allowed. If a service dereferences `user` unconditionally, wrap: create a lightweight system marker. Verify in Step 4; if a None-deref occurs, change `_system_user` to return a cached `User` with username `"camera"` (get_or_create, is_active False) and adjust the test expectations accordingly.

- [ ] **Step 4: Run to verify pass**

Run: `pytest webhooks/tests/test_webhook.py -v`
Expected: all PASS. If a `None` user deref appears, apply the Step-3 note fix (system user) and re-run.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(webhooks): process_webhook dispatch to arrive/load/ship + call log"
```

---

### Task 5: Webhook endpoint (key auth) + URLs

**Files:**
- Create: `backend/webhooks/views.py` (CameraWebhookView part), `backend/webhooks/urls.py`
- Modify: `backend/config/urls.py`
- Test: `backend/webhooks/tests/test_webhook_endpoint.py`

**Interfaces:**
- Consumes: `process_webhook`, `Camera`.
- Produces: `POST /api/webhook/camera/` — body `{camera_id, plate, bags?, weight_kg?}`, header `X-Camera-Key`. 404 unknown camera_id, 401 bad key, 403 inactive, else 200 with rendered template.

- [ ] **Step 1: Write failing tests**

`backend/webhooks/tests/test_webhook_endpoint.py`:
```python
import pytest
from rest_framework.test import APIClient
from webhooks.models import Camera

pytestmark = pytest.mark.django_db


def _cam(**kw):
    d = dict(name="g", camera_id="gate-01", kind="entry", api_key="secret123",
             response_template="", is_active=True)
    d.update(kw)
    return Camera.objects.create(**d)


def test_unknown_camera_404():
    c = APIClient()
    r = c.post("/api/webhook/camera/", {"camera_id": "nope", "plate": "X"}, format="json")
    assert r.status_code == 404


def test_bad_key_401():
    _cam()
    c = APIClient()
    r = c.post("/api/webhook/camera/", {"camera_id": "gate-01", "plate": "X"},
               format="json", HTTP_X_CAMERA_KEY="wrong")
    assert r.status_code == 401


def test_inactive_403():
    _cam(is_active=False)
    c = APIClient()
    r = c.post("/api/webhook/camera/", {"camera_id": "gate-01", "plate": "X"},
               format="json", HTTP_X_CAMERA_KEY="secret123")
    assert r.status_code == 403


def test_valid_call_200_deny_when_no_order():
    _cam()
    c = APIClient()
    r = c.post("/api/webhook/camera/", {"camera_id": "gate-01", "plate": "999ZZ99"},
               format="json", HTTP_X_CAMERA_KEY="secret123")
    assert r.status_code == 200
    assert r.data["decision"] == "deny"
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest webhooks/tests/test_webhook_endpoint.py -v`
Expected: FAIL (no URL).

- [ ] **Step 3: Implement the view + urls**

`backend/webhooks/views.py`:
```python
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from .models import Camera
from .services import process_webhook


class CameraWebhookView(APIView):
    authentication_classes = []      # no JWT
    permission_classes = [AllowAny]  # key auth instead

    def post(self, request):
        camera_id = request.data.get("camera_id")
        camera = Camera.objects.filter(camera_id=camera_id).first()
        if camera is None:
            return Response({"detail": "Камера не найдена", "code": "camera_not_found"}, status=404)
        key = request.headers.get("X-Camera-Key", "")
        if key != camera.api_key:
            return Response({"detail": "Неверный ключ камеры", "code": "bad_key"}, status=401)
        if not camera.is_active:
            return Response({"detail": "Камера отключена", "code": "camera_inactive"}, status=403)
        response = process_webhook(camera, request.data)
        return Response(response, status=200)
```

`backend/webhooks/urls.py`:
```python
from django.urls import path
from .views import CameraWebhookView

urlpatterns = [
    path("webhook/camera/", CameraWebhookView.as_view()),
]
```

In `backend/config/urls.py` add: `path("api/", include("webhooks.urls")),`.

- [ ] **Step 4: Run to verify pass**

Run: `pytest webhooks/tests/test_webhook_endpoint.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(webhooks): key-authenticated camera webhook endpoint"
```

---

### Task 6: Camera CRUD API + simulate + regenerate

**Files:**
- Create: `backend/webhooks/serializers.py`; extend `backend/webhooks/views.py`, `backend/webhooks/urls.py`
- Test: `backend/webhooks/tests/test_cameras_api.py`

**Interfaces:**
- Consumes: `PermViewSetMixin`, `HasPerm`, `process_webhook`, `render_template`.
- Produces:
  - `/api/cameras/` ViewSet (RBAC: view/manage). `api_key` masked on read (`••••last4`), full only in the create response and the `regenerate` action.
  - `POST /api/cameras/{id}/regenerate_key/` → new full key.
  - `POST /api/cameras/{id}/simulate/` (body `{plate, bags?, weight_kg?}`) → renders the response WITHOUT mutating any order (dry-run): returns `{response, decision, reason, order_id}`.
  - `GET /api/cameras/{id}/calls/` → recent WebhookCalls for the camera.
  - `GET /api/webhook-calls/` → all calls (view perm), filter `?camera=`.

- [ ] **Step 1: Write failing tests**

`backend/webhooks/tests/test_cameras_api.py`:
```python
import pytest
from webhooks.models import Camera

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(auth_client, make_user):
    u = make_user(username="root"); u.is_superuser = True; u.save()
    return auth_client(u)


def test_create_camera_returns_full_key(admin_client):
    r = admin_client.post("/api/cameras/", {
        "name": "Ворота", "camera_id": "gate-01", "kind": "entry",
        "response_template": "",
    }, format="json")
    assert r.status_code == 201
    assert len(r.data["api_key"]) >= 24  # full key on create
    assert Camera.objects.get(camera_id="gate-01").kind == "entry"


def test_list_masks_key(admin_client):
    admin_client.post("/api/cameras/", {"name": "g", "camera_id": "gate-01", "kind": "entry"}, format="json")
    r = admin_client.get("/api/cameras/")
    assert r.status_code == 200
    assert r.data[0]["api_key"].startswith("•")


def test_regenerate_key(admin_client):
    admin_client.post("/api/cameras/", {"name": "g", "camera_id": "gate-01", "kind": "entry"}, format="json")
    cam = Camera.objects.get()
    old = cam.api_key
    r = admin_client.post(f"/api/cameras/{cam.id}/regenerate_key/")
    assert r.status_code == 200 and r.data["api_key"] != old


def test_simulate_does_not_mutate_order(admin_client, boss):
    from clients.models import Client
    from orders.models import Order
    c = Client.objects.create(first_name="И", last_name="П", phone="x")
    o = Order.objects.create(client=c, status="paid", truck_number="123ABC02")
    admin_client.post("/api/cameras/", {"name": "g", "camera_id": "gate-01", "kind": "entry"}, format="json")
    cam = Camera.objects.get()
    r = admin_client.post(f"/api/cameras/{cam.id}/simulate/",
                          {"plate": "123ABC02"}, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "paid"  # unchanged by dry-run


def test_non_manager_cannot_create(auth_client, make_user):
    u = make_user(username="plain")
    r = auth_client(u).post("/api/cameras/", {"name": "g", "camera_id": "g1", "kind": "entry"}, format="json")
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest webhooks/tests/test_cameras_api.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement serializers**

`backend/webhooks/serializers.py`:
```python
from rest_framework import serializers
from .models import Camera, WebhookCall


class CameraSerializer(serializers.ModelSerializer):
    api_key = serializers.SerializerMethodField()

    class Meta:
        model = Camera
        fields = ["id", "name", "camera_id", "kind", "api_key",
                  "response_template", "is_active", "last_seen"]

    def get_api_key(self, obj):
        # masked by default; full key surfaced separately on create/regenerate
        if self.context.get("reveal_key"):
            return obj.api_key
        tail = obj.api_key[-4:] if obj.api_key else ""
        return f"••••{tail}"

    def create(self, validated_data):
        validated_data["api_key"] = Camera.generate_key()
        return super().create(validated_data)


class WebhookCallSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookCall
        fields = ["id", "camera", "plate", "payload_bags", "payload_weight",
                  "matched_order", "decision", "reason", "created_at"]
```

- [ ] **Step 4: Implement views + urls**

Add to `backend/webhooks/views.py`:
```python
from decimal import Decimal
from rest_framework import viewsets, mixins
from rest_framework.decorators import action
from rbac.permissions import PermViewSetMixin
from .models import Camera, WebhookCall
from .serializers import CameraSerializer, WebhookCallSerializer
from .services import normalize_plate, _build_context, _find_order
from .templating import render_template
from shipments.services import record_arrival, record_loading, record_shipment  # noqa
from rest_framework.exceptions import ValidationError


class CameraViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Camera.objects.all()
    serializer_class = CameraSerializer
    required_perms = {
        "list": "cameras.view", "retrieve": "cameras.view", "calls": "cameras.view",
        "create": "cameras.manage", "update": "cameras.manage",
        "partial_update": "cameras.manage", "destroy": "cameras.manage",
        "regenerate_key": "cameras.manage", "simulate": "cameras.manage",
    }

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cam = ser.save()
        out = CameraSerializer(cam, context={"reveal_key": True}).data
        from rest_framework.response import Response
        return Response(out, status=201)

    @action(detail=True, methods=["post"], url_path="regenerate_key")
    def regenerate_key(self, request, pk=None):
        cam = self.get_object()
        cam.api_key = Camera.generate_key()
        cam.save(update_fields=["api_key"])
        from rest_framework.response import Response
        return Response(CameraSerializer(cam, context={"reveal_key": True}).data)

    @action(detail=True, methods=["post"])
    def simulate(self, request, pk=None):
        cam = self.get_object()
        plate = normalize_plate(request.data.get("plate", ""))
        bags = request.data.get("bags")
        weight = request.data.get("weight_kg")
        order = _find_order(plate)
        decision, reason = "deny", ""
        if order is None:
            reason = "Заказ по номеру не найден"
        else:
            # dry-run: validate the would-be action without mutating
            decision, reason = _dry_run(cam.kind, order, bags, weight)
        ctx = _build_context(cam, plate, order, decision, reason, bags, weight)
        try:
            response = render_template(cam.response_template, ctx)
        except ValueError:
            response = render_template("", ctx)
        from rest_framework.response import Response
        return Response({"response": response, "decision": decision,
                         "reason": reason, "order_id": order.id if order else None})

    @action(detail=True, methods=["get"])
    def calls(self, request, pk=None):
        qs = self.get_object().calls.all()[:50]
        return self._respond_calls(qs)

    def _respond_calls(self, qs):
        from rest_framework.response import Response
        return Response(WebhookCallSerializer(qs, many=True).data)


def _dry_run(kind, order, bags, weight):
    """Validate without side effects; mirror the service status guards."""
    if kind == "entry":
        if order.status not in ("confirmed", "paid"):
            return "deny", "Машину можно принять только для подтверждённого заказа"
        if not order.is_fully_paid:
            return "deny", "Заказ не оплачен — въезд запрещён"
        return "allow", ""
    if kind == "counter":
        if order.status != "arrived":
            return "deny", "Загрузка возможна только после прибытия"
        return "allow", ""
    if kind == "exit":
        if order.status != "loading":
            return "deny", "Выезд возможен только во время загрузки"
        return "allow", ""
    return "deny", "Неизвестный тип камеры"


class WebhookCallViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = WebhookCallSerializer
    required_perms = {"list": "cameras.view"}

    def get_queryset(self):
        qs = WebhookCall.objects.select_related("camera")
        cam = self.request.query_params.get("camera")
        return qs.filter(camera_id=cam) if cam else qs
```

Update `backend/webhooks/urls.py`:
```python
from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import CameraWebhookView, CameraViewSet, WebhookCallViewSet

router = DefaultRouter()
router.register("cameras", CameraViewSet)
router.register("webhook-calls", WebhookCallViewSet, basename="webhook-calls")

urlpatterns = [
    path("webhook/camera/", CameraWebhookView.as_view()),
] + router.urls
```

- [ ] **Step 5: Run to verify pass + full suite**

Run:
```bash
pytest webhooks/ -v
pytest -q
```
Expected: all webhooks tests PASS; full suite PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(webhooks): camera CRUD, regenerate key, dry-run simulate, call log API"
```

---

### Task 7: Frontend — Камеры list + create modal + nav

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/components/layout/sidebar.tsx`
- Create: `frontend/src/app/management/cameras/page.tsx`

**Interfaces:**
- Consumes: `/api/cameras/`, `can()`, `Modal`, `useApi`.
- Produces: Камеры list (kind badge, masked key, last_seen) + create modal (name, camera_id, kind, template); nav item under Управление gated by `cameras.view`.

- [ ] **Step 1: Add types**

In `frontend/src/lib/types.ts`:
```typescript
export interface Camera {
  id: number; name: string; camera_id: string;
  kind: "entry" | "counter" | "exit";
  api_key: string; response_template: string;
  is_active: boolean; last_seen: string | null;
}
export interface WebhookCall {
  id: number; camera: number; plate: string;
  payload_bags: number | null; payload_weight: string | null;
  matched_order: number | null; decision: string; reason: string; created_at: string;
}
```

- [ ] **Step 2: Add nav item**

In `frontend/src/components/layout/sidebar.tsx`, in the «Управление» section items array, add after the Журнал item:
```typescript
      { href: "/management/cameras", label: "Камеры", icon: Video, perm: "cameras.view" },
```
Add `Video` to the lucide-react import.

- [ ] **Step 3: Write the cameras list page**

`frontend/src/app/management/cameras/page.tsx`:
```tsx
"use client";
import { useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { Plus } from "lucide-react";
import type { Camera } from "@/lib/types";

const KIND_LABELS: Record<string, string> = {
  entry: "Въезд", counter: "Счётчик", exit: "Выезд",
};

export default function CamerasPage() {
  const { data: cameras, reload } = useApi<Camera[]>("/cameras/");
  const { me } = useAuth();
  const canManage = can(me, "cameras.manage");
  const empty = { name: "", camera_id: "", kind: "entry", response_template: "" };
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(empty);
  const [createdKey, setCreatedKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const { data } = await api.post("/cameras/", form);
      setCreatedKey(data.api_key);
      setForm(empty); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <AppShell title="Камеры" section="Управление"
      description="Камеры поста отгрузки: вебхук по номеру машины, настраиваемый ответ и журнал вызовов.">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-[var(--muted-foreground)]">{cameras?.length ?? 0} камер</p>
        {canManage && <Button size="sm" onClick={() => { setError(""); setCreatedKey(""); setOpen(true); }}>
          <Plus className="size-4" /> Добавить камеру</Button>}
      </div>
      <Card><CardContent className="pt-6">
        <Table>
          <THead><TR><TH>Камера</TH><TH>ID</TH><TH>Тип</TH><TH>Ключ</TH><TH>Статус</TH></TR></THead>
          <TBody>
            {(cameras ?? []).map((c) => (
              <TR key={c.id}>
                <TD className="font-medium">
                  <Link href={`/management/cameras/${c.id}`} className="hover:underline">{c.name}</Link>
                </TD>
                <TD className="tabular-nums">{c.camera_id}</TD>
                <TD><Badge tone="muted">{KIND_LABELS[c.kind]}</Badge></TD>
                <TD className="font-mono text-xs text-[var(--muted-foreground)]">{c.api_key}</TD>
                <TD><Badge tone={c.is_active ? "success" : "muted"}>
                  {c.is_active ? "Активна" : "Отключена"}</Badge></TD>
              </TR>
            ))}
            {(cameras ?? []).length === 0 && (
              <TR><TD colSpan={5} className="py-4 text-center text-[var(--muted-foreground)]">Камер пока нет.</TD></TR>)}
          </TBody>
        </Table>
      </CardContent></Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новая камера" className="max-w-lg">
        {createdKey ? (
          <div className="flex flex-col gap-4">
            <p className="text-sm">Камера создана. Сохраните ключ — он показывается один раз:</p>
            <code className="block break-all rounded-md border bg-[var(--muted)] p-3 text-xs">{createdKey}</code>
            <div className="flex justify-end">
              <Button onClick={() => { setOpen(false); setCreatedKey(""); }}>Готово</Button>
            </div>
          </div>
        ) : (
          <form onSubmit={submit} className="flex flex-col gap-4">
            <div className="grid gap-2"><Label>Название</Label>
              <Input value={form.name} required onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
            <div className="grid gap-2"><Label>ID камеры (напр. gate-01)</Label>
              <Input value={form.camera_id} required
                onChange={(e) => setForm({ ...form, camera_id: e.target.value })} /></div>
            <div className="grid gap-2"><Label>Тип</Label>
              <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                <option value="entry">Въезд</option>
                <option value="counter">Счётчик загрузки</option>
                <option value="exit">Выезд</option>
              </Select></div>
            <div className="grid gap-2"><Label>Шаблон ответа (JSON, необязательно)</Label>
              <textarea value={form.response_template} rows={3}
                placeholder='{"open": {{allowed}}, "order": {{order_id}}}'
                onChange={(e) => setForm({ ...form, response_template: e.target.value })}
                className="rounded-md border bg-transparent px-3 py-2 font-mono text-xs outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/40" /></div>
            {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
            <div className="flex justify-end gap-2 border-t pt-4">
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>Отмена</Button>
              <Button type="submit" disabled={busy}>{busy ? "Создание…" : "Создать"}</Button>
            </div>
          </form>
        )}
      </Modal>
    </AppShell>
  );
}
```

- [ ] **Step 4: Typecheck + build**

Run:
```bash
cd frontend && npx tsc --noEmit && npm run build
```
Expected: tsc exit 0; `/management/cameras` route present.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(frontend): cameras list + create modal + nav"
```

---

### Task 8: Frontend — camera detail (template editor, simulator, call log)

**Files:**
- Create: `frontend/src/app/management/cameras/[id]/page.tsx`

**Interfaces:**
- Consumes: `/api/cameras/{id}/`, `/api/cameras/{id}/simulate/`, `/api/cameras/{id}/regenerate_key/`, `/api/cameras/{id}/calls/`.
- Produces: detail page with editable template (PATCH), key reveal/regenerate, simulator form, and call log table.

- [ ] **Step 1: Write the detail page**

`frontend/src/app/management/cameras/[id]/page.tsx`:
```tsx
"use client";
import { use, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import type { Camera, WebhookCall } from "@/lib/types";

const VARS = ["camera_id", "decision", "allowed", "reason", "order_id",
  "plate", "client_name", "bags", "weight_kg", "net_weight_kg"];

export default function CameraDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: cam, reload } = useApi<Camera>(`/cameras/${id}/`);
  const { data: calls, reload: reloadCalls } = useApi<WebhookCall[]>(`/cameras/${id}/calls/`);
  const [tpl, setTpl] = useState<string | null>(null);
  const [simPlate, setSimPlate] = useState("");
  const [simBags, setSimBags] = useState("");
  const [simResult, setSimResult] = useState<unknown>(null);
  const [revealKey, setRevealKey] = useState("");
  const [error, setError] = useState("");

  if (!cam) return <AppShell title="Камера"><p className="text-sm text-[var(--muted-foreground)]">Загрузка…</p></AppShell>;
  const template = tpl ?? cam.response_template;

  async function saveTpl() {
    setError("");
    try { await api.patch(`/cameras/${id}/`, { response_template: template }); reload(); }
    catch (e) { setError(apiError(e)); }
  }
  async function regenerate() {
    try { const { data } = await api.post(`/cameras/${id}/regenerate_key/`); setRevealKey(data.api_key); reload(); }
    catch (e) { setError(apiError(e)); }
  }
  async function simulate() {
    setError("");
    try {
      const { data } = await api.post(`/cameras/${id}/simulate/`, {
        plate: simPlate, bags: simBags ? Number(simBags) : undefined,
      });
      setSimResult(data);
    } catch (e) { setError(apiError(e)); }
  }

  return (
    <AppShell title={cam.name} section="Камеры">
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Параметры</CardTitle>
            <Badge tone={cam.is_active ? "success" : "muted"}>{cam.is_active ? "Активна" : "Отключена"}</Badge>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm">
            <div className="flex justify-between"><span className="text-[var(--muted-foreground)]">ID</span><span className="font-mono">{cam.camera_id}</span></div>
            <div className="flex justify-between"><span className="text-[var(--muted-foreground)]">Ключ</span>
              <span className="font-mono text-xs">{revealKey || cam.api_key}</span></div>
            <Button size="sm" variant="outline" onClick={regenerate}>Перегенерировать ключ</Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Симулятор вызова</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Input placeholder="Номер машины" value={simPlate} onChange={(e) => setSimPlate(e.target.value)} />
            {cam.kind === "counter" && (
              <Input type="number" placeholder="Мешков" value={simBags} onChange={(e) => setSimBags(e.target.value)} />)}
            <Button size="sm" onClick={simulate} disabled={!simPlate}>Симулировать</Button>
            {simResult != null && (
              <pre className="overflow-x-auto rounded-md border bg-[var(--muted)] p-3 text-xs">
                {JSON.stringify(simResult, null, 2)}
              </pre>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader><CardTitle>Шаблон ответа</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-xs text-[var(--muted-foreground)]">
            Переменные: {VARS.map((v) => <code key={v} className="mr-1 rounded bg-[var(--muted)] px-1">{`{{${v}}}`}</code>)}
          </p>
          <textarea value={template} rows={4}
            onChange={(e) => setTpl(e.target.value)}
            className="rounded-md border bg-transparent px-3 py-2 font-mono text-xs outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/40" />
          {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
          <div className="flex justify-end"><Button size="sm" onClick={saveTpl}>Сохранить шаблон</Button></div>
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>Журнал вызовов</CardTitle>
          <Button size="sm" variant="ghost" onClick={() => reloadCalls()}>Обновить</Button>
        </CardHeader>
        <CardContent>
          <Table>
            <THead><TR><TH>Время</TH><TH>Номер</TH><TH>Решение</TH><TH>Заказ</TH><TH>Причина</TH></TR></THead>
            <TBody>
              {(calls ?? []).map((c) => (
                <TR key={c.id}>
                  <TD className="whitespace-nowrap text-[var(--muted-foreground)]">{new Date(c.created_at).toLocaleString("ru-RU")}</TD>
                  <TD className="tabular-nums">{c.plate}</TD>
                  <TD><Badge tone={c.decision === "allow" ? "success" : "destructive"}>{c.decision}</Badge></TD>
                  <TD>{c.matched_order ? `#${c.matched_order}` : "—"}</TD>
                  <TD className="text-[var(--muted-foreground)]">{c.reason || "—"}</TD>
                </TR>
              ))}
              {(calls ?? []).length === 0 && (
                <TR><TD colSpan={5} className="py-4 text-center text-[var(--muted-foreground)]">Вызовов пока нет.</TD></TR>)}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </AppShell>
  );
}
```

- [ ] **Step 2: Typecheck + build**

Run:
```bash
cd frontend && npx tsc --noEmit && npm run build
```
Expected: tsc exit 0; `/management/cameras/[id]` route present.

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "feat(frontend): camera detail — template editor, simulator, call log"
```

---

### Task 9: Full-stack verification in Docker

**Files:** none (verification).

- [ ] **Step 1: Rebuild + up**

```bash
cd /Users/dimash/PycharmProjects/asyl-ltd
docker compose build backend frontend && docker compose up -d
sleep 14
```

- [ ] **Step 2: Create a camera (admin) and capture key**

Via API as admin (.env creds): `POST /api/cameras/` (gate-01, entry) → 201; save `api_key`.

- [ ] **Step 3: Fire the webhook**

`POST /api/webhook/camera/` with `{camera_id:"gate-01", plate:"<a paid order's truck>"}` and header `X-Camera-Key: <key>`.
Expected: 200; if a paid order matches → `decision allow` and that order becomes `arrived`; else `deny`.

- [ ] **Step 4: Bad key + unknown camera**

Same endpoint with wrong key → 401; unknown `camera_id` → 404.

- [ ] **Step 5: Simulate is non-mutating**

`POST /api/cameras/{id}/simulate/` with a paid order's plate → returns `decision allow` but the order status stays `paid` (not arrived).

- [ ] **Step 6: Pages serve**

`/management/cameras` and `/management/cameras/<id>` → 200.

- [ ] **Step 7: Final commit**

```bash
git add -A && git commit -m "chore: verify camera webhooks end-to-end" --allow-empty
```

---

## Self-Review Notes (coverage map)

- §1 models (Camera, WebhookCall, plate normalization) → Tasks 2, 4.
- §2 endpoint (camera_id+key auth order, dispatch by kind, 200/401/403/404) → Tasks 4, 5.
- §3 template variables + safe substitution + default + invalid→error → Task 3.
- §4 UI (list, create+key, detail template/simulator/call log, nav) → Tasks 7, 8.
- §5 RBAC cameras.view/manage → Task 1, 6; transaction + masked key + dry-run simulate + webhook outside JWT → Tasks 4, 5, 6; tests → every task.
- Out of scope (node editor, HMAC, incremental bags) → not implemented (correct).
