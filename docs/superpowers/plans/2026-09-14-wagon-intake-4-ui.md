# Wagon Intake · Part 4 — CRM UI: arch zone editor, contour status, stops journal

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operators can draw the arch zone on the live cam8 frame, see whether the contour is alive (wagon scale, camera motion, collector, last stop), watch the wagon scale in the intake toolbar, and open a journal of wagon stops with the reason whenever a stop waits for them.

**Architecture:** One backend proxy view (`/cameras/wagon-arch-runtime/`) mirrors `VehiclePlateRuntimeView` and reuses its projection helpers; the frontend reuses `VehicleRoiOverlay` (labels changed), `CameraStream`, `LiveScaleStatus` (scale key widened to `wagon`) and the `PassageHistory` list pattern. No new design language — Stripe/Linear-quiet, existing tokens (see memory `design-real-references`).

**Tech Stack:** Django/DRF (proxy view), Next.js + TypeScript + vitest/testing-library.

**Spec:** `docs/superpowers/specs/2026-09-12-wagon-intake-arch-design.md` (section «4. Интерфейс CRM»).

**Repository:** `/Users/dimash/PycharmProjects/asyl-ltd`. Backend tests: `cd backend && .venv/bin/pytest <path> -q -p no:cacheprovider`. Frontend: `cd frontend && npx vitest run <files>`; before finishing: `npm run check` and `npm run build` (memory `ci-run-check-not-parts`).

## Global Constraints

- Zone payload shape on the wire is the vehicle-ROI shape: `{cam, configured, enabled, source, coordinate_space: "normalized", points: [{x, y}], updated_at}`; PUT body `{points: [{x, y}], enabled: true, source: "main"}`; 3–12 points; a 503 with `saved: true` means «saved, monitor refresh pending» and is accepted by the UI exactly like the vehicle ROI flow.
- Permissions: GET `grain.view`; PUT superuser only (same as `vehicle-plate-runtime`). The frontend shows the editor only to superusers (`useAuth(state => Boolean(state.me?.is_superuser))`).
- The browser stream name for cam8 is the bare camera name (`cam8`) — the same `CameraStream src` that `WagonNumberCameraWorkspace` already uses (`settings.camera_source`).
- Polling: camera runtime every 5 s, paused while editing/saving; stops journal every 5 s on the newest page only; wagon scale status every 3 s (existing `LiveScaleStatus`).
- Russian labels: zone «ЗОНА АРКИ» / «Редактор зоны арки» / «Точка зоны»; motion «Вагон едет» / «Вагон стоит · N с» / «Нет данных о движении»; tab «Стоянки под аркой»; toolbar scale label «Приход».
- Reason labels (`archStopReasonLabel`): `silo_required` → «Назначьте силос в рейсе — заезд запишется автоматически»; `wagon_on_site` → «Вагон с этим номером уже на территории»; `exit_not_lower` → «Вес на выезде не меньше веса на въезде — проверьте взвешивания»; `no_exit_weight` → «Перед отъездом не было устойчивого веса — укажите выезд вручную»; `invalid_wagon_transition` → «Рейс в состоянии, где вес применить нельзя»; anything else → the server `detail` or «Требуется проверка».

---

## File structure

| File | Responsibility |
|---|---|
| `backend/apps/cameras/api_views/wagon_arch_runtime.py` | GET/PUT proxy: zone + motion + importer runtime |
| `backend/apps/cameras/urls.py` | two routes |
| `backend/apps/cameras/tests/test_wagon_arch_runtime.py` | proxy tests |
| `frontend/src/lib/types.ts` | `WagonArchMotion`, `WagonArchRuntime`, `WagonArchCameraRuntime`, `WagonArchStop` |
| `frontend/src/lib/weighing-evidence.ts` | `archStopReasonLabel` |
| `frontend/src/components/grain/live-scale-status.tsx` | `scaleKey: "truck" \| "wagon"` |
| `frontend/src/components/grain/grain-toolbar.tsx` | wagon scale in the intake toolbar |
| `frontend/src/components/grain/wagon-arch-camera.tsx` | zone editor + contour status panel |
| `frontend/src/components/grain/wagon-number-camera.tsx` | mounts the panel |
| `frontend/src/components/grain/wagon-arch-stops.tsx` | stops journal |
| `frontend/src/components/grain/grain-workspace.tsx` | intake tab «Стоянки под аркой» |
| tests next to each component | |

---

### Task 1: Backend proxy `GET/PUT /cameras/wagon-arch-runtime/`

**Files:**
- Create: `backend/apps/cameras/api_views/wagon_arch_runtime.py`
- Modify: `backend/apps/cameras/urls.py` (next to the `vehicle-plate-runtime` routes ~line 122)
- Test: `backend/apps/cameras/tests/test_wagon_arch_runtime.py`

**Interfaces:**
- Consumes: `ai.arch_zone`, `ai.arch_motion`, `ai.save_arch_zone`, `ai.enabled`, `ai.camera_id` (Part 2 Task 3); `wagon_arch.runtime()` (Part 3); `_project_roi`, `project_vehicle_roi_update`, `project_vehicle_roi_save_response`, `_browser_stream`, `_error_response`, `VehicleRuntimeContractError` from `apps/cameras/api_views/vehicle_runtime.py`.
- Produces GET payload:

```python
{
  "camera": "cam8", "source": "main", "stream": "cam8",
  "automation_enabled": bool,                    # settings.WAGON_ARCH_AUTOMATION_ENABLED
  "zone": {cam, configured, enabled, source, coordinate_space, points, updated_at},   # unconfigured shape when AI is off
  "motion": {"state": "moving|still|unknown", "still_seconds": float, "direction": str, "status": str, "sample_age_seconds": float|None} | None,
  "runtime": wagon_arch.runtime(),
  "diagnostic": "" | "AI-сервис камер не настроен" | "Зона арки недоступна: <detail>" | "Движение недоступно: <detail>",
}
```

  PUT (superuser) → `{"saved": true, "applied_to_monitor": bool, "zone": {...}}` with status 200, or 503 plus `{"detail": "Зона сохранена, но монитор пока не применил обновление", "code": "zone_saved_refresh_pending"}`; errors `{"detail", "code"}` with codes `ai_disabled` (503), `invalid_arch_zone` (400), `ai_error` (400/404 pass-through, else 502), `ai_unavailable` (502), `ai_invalid_response` (502).

- [ ] **Step 1: Write the failing tests**

Create `backend/apps/cameras/tests/test_wagon_arch_runtime.py`:

```python
from unittest.mock import patch

import pytest

from apps.cameras import ai

pytestmark = pytest.mark.django_db

ZONE = {"cam": "cam8", "configured": True, "enabled": True, "source": "main", "coordinate_space": "normalized",
        "points": [{"x": 0.1, "y": 0.3}, {"x": 0.9, "y": 0.3}, {"x": 0.9, "y": 0.7}, {"x": 0.1, "y": 0.7}],
        "updated_at": "2026-09-14T05:00:00+00:00"}
MOTION = {"cam": "cam8", "source": "main", "status": "online", "state": "still", "still_seconds": 12.5,
          "direction": "right", "moving_fraction": 0.01, "sample_age_seconds": 0.3, "samples": 40}


@pytest.fixture
def viewer(user_with_perms):
    return user_with_perms("arch-viewer", codes=["grain.view"])


@pytest.fixture
def superuser(make_user):
    user = make_user("arch-admin")
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    return user


def test_runtime_projects_zone_motion_and_importer_state(auth_client, viewer, settings):
    settings.WAGON_ARCH_AUTOMATION_ENABLED = True
    with patch.object(ai, "enabled", return_value=True), patch.object(ai, "arch_zone", return_value=ZONE), \
            patch.object(ai, "arch_motion", return_value=MOTION), \
            patch("apps.grain.wagon_arch.runtime", return_value={"enabled": True, "camera": "cam8", "collector": None,
                                                                 "pending_stops": 0, "attention_stops": 0, "last_stop": None, "updated_at": None}):
        response = auth_client(viewer).get("/api/cameras/wagon-arch-runtime/")
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert (response.data["camera"], response.data["stream"], response.data["automation_enabled"]) == ("cam8", "cam8", True)
    assert response.data["zone"]["points"] == ZONE["points"]
    assert response.data["motion"] == {"state": "still", "still_seconds": 12.5, "direction": "right", "status": "online", "sample_age_seconds": 0.3}
    assert response.data["runtime"]["camera"] == "cam8" and response.data["diagnostic"] == ""


def test_runtime_degrades_when_the_camera_pc_is_off(auth_client, viewer):
    with patch.object(ai, "enabled", return_value=False):
        response = auth_client(viewer).get("/api/cameras/cam8/wagon-arch-runtime/")
    assert response.status_code == 200
    assert response.data["zone"]["configured"] is False and response.data["motion"] is None
    assert response.data["diagnostic"] == "AI-сервис камер не настроен"


def test_runtime_reports_motion_errors_without_hiding_the_zone(auth_client, viewer):
    with patch.object(ai, "enabled", return_value=True), patch.object(ai, "arch_zone", return_value=ZONE), \
            patch.object(ai, "arch_motion", side_effect=ai.AiError(404, "not configured")):
        response = auth_client(viewer).get("/api/cameras/wagon-arch-runtime/")
    assert response.status_code == 200
    assert response.data["zone"]["configured"] is True and response.data["motion"] is None
    assert response.data["diagnostic"].startswith("Движение недоступно")


def test_put_requires_superuser_and_delegates_to_the_camera_pc(auth_client, viewer, superuser):
    body = {"points": ZONE["points"], "enabled": True, "source": "main"}
    assert auth_client(viewer).put("/api/cameras/cam8/wagon-arch-runtime/", body, format="json").status_code == 403
    upstream = {"ok": True, "saved": True, "applied_to_monitor": True, **ZONE}
    with patch.object(ai, "enabled", return_value=True), patch.object(ai, "save_arch_zone", return_value=(200, upstream)) as save:
        response = auth_client(superuser).put("/api/cameras/cam8/wagon-arch-runtime/", body, format="json")
    assert response.status_code == 200
    assert response.data["saved"] is True and response.data["applied_to_monitor"] is True
    assert response.data["zone"]["points"] == ZONE["points"]
    assert save.call_args.args[0] == "cam8" and save.call_args.args[1]["points"] == ZONE["points"]
    with patch.object(ai, "enabled", return_value=True), patch.object(ai, "save_arch_zone", return_value=(503, {**upstream, "applied_to_monitor": False, "error": "monitor unavailable"})):
        pending = auth_client(superuser).put("/api/cameras/cam8/wagon-arch-runtime/", body, format="json")
    assert pending.status_code == 503 and pending.data["saved"] is True and pending.data["code"] == "zone_saved_refresh_pending"
    with patch.object(ai, "enabled", return_value=True):
        assert auth_client(superuser).put("/api/cameras/cam8/wagon-arch-runtime/", {"points": [{"x": 0, "y": 0}]}, format="json").data["code"] == "invalid_arch_zone"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/bin/pytest apps/cameras/tests/test_wagon_arch_runtime.py -q -p no:cacheprovider`
Expected: FAIL with 404 responses (no route yet).

- [ ] **Step 3: Implement the view and routes**

Create `backend/apps/cameras/api_views/wagon_arch_runtime.py`:

```python
"""Browser-facing view of the wagon arch: zone polygon, motion state, importer runtime."""
from typing import ClassVar

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsSuperUser, PermAPIViewMixin
from apps.grain import wagon_arch

from .. import ai
from .vehicle_runtime import (
    VehicleRuntimeContractError,
    _browser_stream,
    _error_response,
    _project_roi,
    project_vehicle_roi_save_response,
    project_vehicle_roi_update,
)

MOTION_STATES = {"moving", "still", "unknown"}


def _project_motion(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    state = value.get("state")
    still = value.get("still_seconds")
    age = value.get("sample_age_seconds")
    if state not in MOTION_STATES or isinstance(still, bool) or not isinstance(still, (int, float)) or still < 0:
        raise VehicleRuntimeContractError("arch motion payload is malformed")
    if age is not None and (isinstance(age, bool) or not isinstance(age, (int, float)) or age < 0):
        raise VehicleRuntimeContractError("arch motion age is malformed")
    return {
        "state": state,
        "still_seconds": float(still),
        "direction": str(value.get("direction") or ""),
        "status": str(value.get("status") or ""),
        "sample_age_seconds": float(age) if age is not None else None,
    }


def _empty_zone(camera: str) -> dict:
    return _project_roi({"cam": camera, "configured": False, "enabled": False, "source": "main",
                         "coordinate_space": "normalized", "points": [], "updated_at": None}, camera)


class WagonArchRuntimeView(PermAPIViewMixin, APIView):
    required_perms: ClassVar[dict[str, str]] = {"get": "grain.view"}

    def get_permissions(self):
        if self.request.method.lower() == "put":
            return [IsSuperUser()]
        return super().get_permissions()

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def get(self, request, cam=None):
        camera = settings.WAGON_ARCH_CAMERA if cam is None else ai.camera_id(cam)
        payload = {
            "camera": camera, "source": "main", "stream": _browser_stream(camera, "main"),
            "automation_enabled": bool(settings.WAGON_ARCH_AUTOMATION_ENABLED),
            "zone": _empty_zone(camera), "motion": None, "runtime": wagon_arch.runtime(), "diagnostic": "",
        }
        if not ai.enabled():
            payload["diagnostic"] = "AI-сервис камер не настроен"
            return Response(payload)
        problems = []
        try:
            payload["zone"] = _project_roi(ai.arch_zone(camera), camera)
        except (ai.AiUnavailable, ai.AiError, VehicleRuntimeContractError) as exc:
            problems.append(f"Зона арки недоступна: {getattr(exc, 'detail', exc)}")
        try:
            payload["motion"] = _project_motion(ai.arch_motion(camera))
        except (ai.AiUnavailable, ai.AiError, VehicleRuntimeContractError) as exc:
            problems.append(f"Движение недоступно: {getattr(exc, 'detail', exc)}")
        payload["diagnostic"] = "; ".join(problems)
        return Response(payload)

    def put(self, request, cam: str):
        if not ai.enabled():
            return _error_response("AI-сервис камер не настроен", "ai_disabled", status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            camera = ai.camera_id(cam)
            update = project_vehicle_roi_update(request.data, expected_source="main")
        except VehicleRuntimeContractError:
            return _error_response("Некорректная зона арки", "invalid_arch_zone", status.HTTP_400_BAD_REQUEST)
        except ai.AiError:
            return _error_response("Неизвестная камера", "ai_error", status.HTTP_400_BAD_REQUEST)
        try:
            upstream_status, upstream_payload = ai.save_arch_zone(camera, update)
        except ai.AiUnavailable:
            return _error_response("ПК камер недоступен", "ai_unavailable", status.HTTP_502_BAD_GATEWAY)
        if upstream_status in (status.HTTP_200_OK, status.HTTP_503_SERVICE_UNAVAILABLE):
            try:
                zone = _project_roi(upstream_payload, camera)
            except VehicleRuntimeContractError:
                return _error_response("AI-сервис вернул некорректный результат сохранения зоны", "ai_invalid_response", status.HTTP_502_BAD_GATEWAY)
            payload = {"saved": bool(upstream_payload.get("saved", True)), "applied_to_monitor": bool(upstream_payload.get("applied_to_monitor", False)), "zone": zone}
            if upstream_status == status.HTTP_503_SERVICE_UNAVAILABLE:
                payload.update({"detail": "Зона сохранена, но монитор пока не применил обновление", "code": "zone_saved_refresh_pending"})
            return Response(payload, status=upstream_status)
        response_status = upstream_status if upstream_status in (400, 404) else status.HTTP_502_BAD_GATEWAY
        return _error_response(str(upstream_payload.get("error") or "ПК камер отклонил зону"), "ai_error", response_status)
```

If `_project_roi` in `vehicle_runtime.py` rejects the upstream PUT payload because of extra keys (`ok`, `saved`, `applied_to_monitor`, `error`), pass only the zone keys: `{k: upstream_payload.get(k) for k in ("cam", "configured", "enabled", "source", "coordinate_space", "points", "updated_at")}`. `project_vehicle_roi_save_response` is imported for parity; remove the import if unused.

`urls.py`: add

```python
    path("cameras/wagon-arch-runtime/", WagonArchRuntimeView.as_view(), name="wagon-arch-runtime-bootstrap"),
    path("cameras/<str:cam>/wagon-arch-runtime/", WagonArchRuntimeView.as_view(), name="wagon-arch-runtime"),
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && .venv/bin/pytest apps/cameras/tests/test_wagon_arch_runtime.py apps/cameras/tests/test_vehicle_plate_runtime.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/cameras/api_views/wagon_arch_runtime.py backend/apps/cameras/urls.py backend/apps/cameras/tests/test_wagon_arch_runtime.py
git commit -m "feat(cameras): wagon-arch runtime proxy (zone, motion, importer state)"
```

---

### Task 2: Types, reason labels, wagon scale in the intake toolbar

**Files:**
- Modify: `frontend/src/lib/types.ts` (append after `WagonNumberCameraSettings` ~line 745)
- Modify: `frontend/src/lib/weighing-evidence.ts` (append)
- Modify: `frontend/src/components/grain/live-scale-status.tsx:68` (`scaleKey` type)
- Modify: `frontend/src/components/grain/grain-toolbar.tsx`
- Test: `frontend/src/components/grain/live-scale-status.test.tsx`, `frontend/src/components/grain/grain-toolbar.test.tsx`, `frontend/src/lib/weighing-evidence.test.ts` (create if absent)

**Interfaces:**
- Produces:

```ts
export interface WagonArchMotion { state: "moving" | "still" | "unknown"; still_seconds: number; direction: string; status: string; sample_age_seconds: number | null }
export interface WagonArchCollector { total: number; pending: number; status: string; standing: string | null; motion: string | null; heartbeat_at: number | null }
export interface WagonArchLastStop { id: number; stop_id: string; number: string; status: WagonArchStopStatus; full_weight_kg: number; exit_weight_kg: number | null; arrived_at: string; wagon_id: number | null; blocked_reason: string }
export interface WagonArchRuntime { enabled: boolean; camera: string; collector: WagonArchCollector | null; pending_stops: number; attention_stops: number; last_stop: WagonArchLastStop | null; updated_at: string | null }
export interface WagonArchCameraRuntime { camera: string; source: "main"; stream: string; automation_enabled: boolean; zone: VehicleRoiConfig; motion: WagonArchMotion | null; runtime: WagonArchRuntime; diagnostic: string }
export type WagonArchStopStatus = "open" | "closed" | "attention" | "superseded";
export interface WagonArchStop { id: number; stop_id: string; camera: string; arrived_at: string; full_weight_kg: number; exit_weight_kg: number | null; net_kg: number | null; number: string; number_source: string; recognition_error: string; ocr_attempts: number; status: WagonArchStopStatus; blocked_reason: string; blocked_detail: string; motion_gap: boolean; departed_at: string | null; entry_applied_at: string | null; exit_applied_at: string | null; wagon_id: number | null; wagon_status: string; photo_url: string | null }
export function archStopReasonLabel(reason: string, detail?: string): string
```

  (`VehicleRoiConfig` is exported from `components/grain/vehicle-roi-overlay.tsx`; import the type there or move it to `types.ts` — keep one definition.)

- [ ] **Step 1: Write the failing tests**

Append to `live-scale-status.test.tsx`:

```ts
it("reads the wagon scale for the intake contour", () => {
  mocks.useApi.mockReturnValue({ data: preview({ state: "ready", weight_kg: "62340" }), loading: false, error: "", reload: mocks.reload });
  render(<LiveScaleStatus active scaleKey="wagon" label="Приход" />);
  expect(mocks.useApi).toHaveBeenCalledWith("/truck-scales/wagon/reading/");
  expect(screen.getByRole("group", { name: /Весы «Приход»: 62,34 т/ })).toBeInTheDocument();
});
```

(Adapt `preview(...)` to the factory that file already defines and its exact aria text format — copy the assertion style of the existing "3,66 т" test.)

Append to `grain-toolbar.test.tsx` (read its existing render helper first and reuse it):

```ts
it("shows the wagon scale for intake weighers even without other actions", () => {
  render(<GrainToolbar direction="intake" canWeigh canArrive={false} canSupply={false} onArrive={vi.fn()} onSupply={vi.fn()} />);
  expect(screen.getByRole("group", { name: "Текущий вес прихода" })).toBeInTheDocument();
  expect(screen.getByTestId("live-scale-status")).toHaveAttribute("data-scale-key", "wagon");
});
```

(match the component's actual prop names — see `GrainToolbar` props at `grain-toolbar.tsx:8-22`; mock `LiveScaleStatus` in that test as `({ scaleKey, label }) => <div data-testid="live-scale-status" data-scale-key={scaleKey} data-label={label} />` the same way `trip-detail` tests mock it.)

Create `frontend/src/lib/weighing-evidence.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { archStopReasonLabel } from "./weighing-evidence";

describe("archStopReasonLabel", () => {
  it("explains what the operator must do", () => {
    expect(archStopReasonLabel("silo_required")).toBe("Назначьте силос в рейсе — заезд запишется автоматически");
    expect(archStopReasonLabel("no_exit_weight")).toBe("Перед отъездом не было устойчивого веса — укажите выезд вручную");
    expect(archStopReasonLabel("something_new", "Текст с сервера")).toBe("Текст с сервера");
    expect(archStopReasonLabel("something_new")).toBe("Требуется проверка");
    expect(archStopReasonLabel("")).toBe("");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/components/grain/live-scale-status.test.tsx src/components/grain/grain-toolbar.test.tsx src/lib/weighing-evidence.test.ts`
Expected: FAIL (type error / missing export / no intake scale group).

- [ ] **Step 3: Implement**

`types.ts`: add the interfaces from the Interfaces block (import `VehicleRoiConfig` type from `@/components/grain/vehicle-roi-overlay` or move that type here and re-export from the overlay file).

`weighing-evidence.ts`:

```ts
const ARCH_STOP_REASONS: Record<string, string> = {
  silo_required: "Назначьте силос в рейсе — заезд запишется автоматически",
  wagon_on_site: "Вагон с этим номером уже на территории",
  exit_not_lower: "Вес на выезде не меньше веса на въезде — проверьте взвешивания",
  no_exit_weight: "Перед отъездом не было устойчивого веса — укажите выезд вручную",
  invalid_wagon_transition: "Рейс в состоянии, где вес применить нельзя",
};

export function archStopReasonLabel(reason: string, detail?: string) {
  if (!reason) return "";
  return ARCH_STOP_REASONS[reason] || detail || "Требуется проверка";
}
```

`live-scale-status.tsx`: `scaleKey: "truck" | "wagon"`.

`grain-toolbar.tsx`: change the early return `if (direction === "intake" && items.length === 0) return null;` to `if (direction === "intake" && items.length === 0 && !canWeigh) return null;` and render, before the intake actions:

```tsx
      {direction === "intake" && canWeigh && (
        <div role="group" aria-label="Текущий вес прихода">
          <LiveScaleStatus active scaleKey="wagon" label="Приход" />
        </div>
      )}
```

(mirror the wrapper markup used for the passage group at lines 38–42).

- [ ] **Step 4: Run the tests and typecheck**

Run: `cd frontend && npx vitest run src/components/grain/live-scale-status.test.tsx src/components/grain/grain-toolbar.test.tsx src/lib/weighing-evidence.test.ts && npx tsc --noEmit`
Expected: PASS, tsc clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/weighing-evidence.ts frontend/src/lib/weighing-evidence.test.ts frontend/src/components/grain/live-scale-status.tsx frontend/src/components/grain/live-scale-status.test.tsx frontend/src/components/grain/grain-toolbar.tsx frontend/src/components/grain/grain-toolbar.test.tsx
git commit -m "feat(grain-ui): wagon scale in the intake toolbar, arch stop types and labels"
```

---

### Task 3: Arch zone editor and contour status panel

**Files:**
- Create: `frontend/src/components/grain/wagon-arch-camera.tsx`
- Modify: `frontend/src/components/grain/wagon-number-camera.tsx` (`WagonNumberCameraWorkspace`, mount the panel after `<CameraPanel …/>`)
- Test: `frontend/src/components/grain/wagon-arch-camera.test.tsx`, `frontend/src/components/grain/wagon-number-camera.test.tsx` (extend)

**Interfaces:**
- Consumes: `GET/PUT /cameras/wagon-arch-runtime/` (Task 1), `VehicleRoiOverlay` + `normalizeVehicleRoi` + `isDrawableVehicleRoi` (+ `NormalizedRoiPoint`), `CameraStream`, `useApi`, `useVisiblePolling`, `api`, `apiError`, `showSuccess`, `useAuth`.
- Produces: `export function WagonArchCameraPanel()` (no props).

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/grain/wagon-arch-camera.test.tsx`:

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WagonArchCameraRuntime } from "@/lib/types";
import { WagonArchCameraPanel } from "./wagon-arch-camera";

const mocks = vi.hoisted(() => ({
  runtime: null as WagonArchCameraRuntime | null,
  reload: vi.fn(),
  setData: vi.fn(),
  polling: vi.fn(),
  put: vi.fn(),
  showSuccess: vi.fn(),
  auth: { isSuperuser: true },
}));
vi.mock("@/components/camera-stream", () => ({
  CameraStream: ({ src }: { src: string }) => <div data-testid="camera-stream" data-src={src} />,
}));
vi.mock("@/lib/use-api", () => ({
  useApi: () => ({ data: mocks.runtime, loading: false, error: "", reload: mocks.reload, setData: mocks.setData }),
}));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: mocks.polling }));
vi.mock("@/lib/api", () => ({ api: { put: mocks.put }, apiError: (e: Error) => e.message }));
vi.mock("@/lib/toast", () => ({ showSuccess: mocks.showSuccess }));
vi.mock("@/store/auth", () => ({
  useAuth: (selector: (state: { me: { is_superuser: boolean; permissions: string[] } }) => unknown) =>
    selector({ me: { is_superuser: mocks.auth.isSuperuser, permissions: [] } }),
}));

const ZONE = {
  cam: "cam8", configured: true, enabled: true, source: "main", coordinate_space: "normalized",
  points: [{ x: 0.1, y: 0.3 }, { x: 0.9, y: 0.3 }, { x: 0.9, y: 0.7 }, { x: 0.1, y: 0.7 }], updated_at: "2026-09-14T05:00:00+00:00",
};

function runtime(overrides: Partial<WagonArchCameraRuntime> = {}): WagonArchCameraRuntime {
  return {
    camera: "cam8", source: "main", stream: "cam8", automation_enabled: true, zone: ZONE,
    motion: { state: "still", still_seconds: 12.5, direction: "right", status: "online", sample_age_seconds: 0.3 },
    runtime: {
      enabled: true, camera: "cam8",
      collector: { total: 12, pending: 0, status: "running", standing: "stop-1", motion: "still", heartbeat_at: 1 },
      pending_stops: 1, attention_stops: 0,
      last_stop: { id: 7, stop_id: "stop-1", number: "28055531", status: "open", full_weight_kg: 62340, exit_weight_kg: null,
                   arrived_at: "2026-09-14T04:00:00Z", wagon_id: 124, blocked_reason: "silo_required" },
      updated_at: "2026-09-14T05:00:00Z",
    },
    diagnostic: "",
    ...overrides,
  };
}

beforeEach(() => {
  mocks.runtime = runtime();
  mocks.auth.isSuperuser = true;
  mocks.put.mockReset();
});

describe("WagonArchCameraPanel", () => {
  it("shows the stream, the zone, the motion state and the collector status", () => {
    render(<WagonArchCameraPanel />);
    expect(screen.getByTestId("camera-stream")).toHaveAttribute("data-src", "cam8");
    expect(screen.getByTestId("vehicle-roi-polygon")).toBeInTheDocument();
    expect(screen.getByText("Вагон стоит · 13 с")).toBeInTheDocument();
    expect(screen.getByText(/Сборщик: работает/)).toBeInTheDocument();
    expect(screen.getByText(/Вагон 28055531/)).toBeInTheDocument();
    expect(screen.getByText("Назначьте силос в рейсе — заезд запишется автоматически")).toBeInTheDocument();
    expect(mocks.polling).toHaveBeenLastCalledWith(mocks.reload, 5000, true);
  });

  it("explains a missing zone and an unavailable camera pc", () => {
    mocks.runtime = runtime({ zone: { ...ZONE, configured: false, enabled: false, points: [] }, motion: null, diagnostic: "Движение недоступно: not configured" });
    render(<WagonArchCameraPanel />);
    expect(screen.getByText("Зона арки не задана")).toBeInTheDocument();
    expect(screen.getByText("Нет данных о движении")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Движение недоступно");
  });

  it("lets a superuser edit and save the zone and keeps a 503 partial save", async () => {
    const user = userEvent.setup();
    mocks.put.mockResolvedValueOnce({ data: { saved: true, applied_to_monitor: true, zone: ZONE } });
    render(<WagonArchCameraPanel />);
    await user.click(screen.getByRole("button", { name: "Изменить зону" }));
    expect(mocks.polling).toHaveBeenLastCalledWith(mocks.reload, 5000, false);
    await user.click(screen.getByRole("button", { name: "Сохранить зону" }));
    expect(mocks.put).toHaveBeenCalledWith(
      "/cameras/cam8/wagon-arch-runtime/",
      { points: ZONE.points, enabled: true, source: "main" },
      { timeout: 12_000 },
    );
    expect(mocks.setData).toHaveBeenCalledWith(expect.objectContaining({ zone: ZONE }));
    expect(mocks.showSuccess).toHaveBeenCalledWith("Зона арки сохранена");

    mocks.put.mockRejectedValueOnce({ response: { status: 503, data: { saved: true, applied_to_monitor: false, zone: ZONE, code: "zone_saved_refresh_pending" } }, message: "503" });
    await user.click(screen.getByRole("button", { name: "Изменить зону" }));
    await user.click(screen.getByRole("button", { name: "Сохранить зону" }));
    expect(screen.getByRole("status")).toHaveTextContent("монитор пока не подтвердил");
  });

  it("hides the editor from non-superusers and shows save errors in place", async () => {
    mocks.auth.isSuperuser = false;
    render(<WagonArchCameraPanel />);
    expect(screen.queryByRole("button", { name: "Изменить зону" })).toBeNull();
  });
});
```

Extend `wagon-number-camera.test.tsx` with `vi.mock("./wagon-arch-camera", () => ({ WagonArchCameraPanel: () => <div data-testid="wagon-arch-panel" /> }))` and an assertion that the workspace renders the panel.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/components/grain/wagon-arch-camera.test.tsx src/components/grain/wagon-number-camera.test.tsx`
Expected: FAIL (`Failed to resolve import "./wagon-arch-camera"`).

- [ ] **Step 3: Implement the panel**

Create `frontend/src/components/grain/wagon-arch-camera.tsx`:

```tsx
"use client";

import { useState } from "react";
import type { AxiosError } from "axios";
import { Check, PencilLine, X } from "lucide-react";
import { CameraStream } from "@/components/camera-stream";
import {
  VehicleRoiOverlay,
  isDrawableVehicleRoi,
  normalizeVehicleRoi,
  type NormalizedRoiPoint,
  type VehicleRoiConfig,
} from "@/components/grain/vehicle-roi-overlay";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ErrorAlert } from "@/components/ui/data-state";
import { api, apiError } from "@/lib/api";
import { formatKg } from "@/lib/grain";
import { showSuccess } from "@/lib/toast";
import type { WagonArchCameraRuntime } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { cn } from "@/lib/utils";
import { archStopReasonLabel } from "@/lib/weighing-evidence";
import { useAuth } from "@/store/auth";

const RUNTIME_URL = "/cameras/wagon-arch-runtime/";
const POLL_MS = 5_000;
const DEFAULT_ZONE: NormalizedRoiPoint[] = [
  [0.1, 0.3],
  [0.9, 0.3],
  [0.9, 0.7],
  [0.1, 0.7],
];

type SaveResponse = { saved: boolean; applied_to_monitor: boolean; zone: VehicleRoiConfig; code?: string };

function draftZone(points: NormalizedRoiPoint[]): VehicleRoiConfig {
  return { configured: true, enabled: true, source: "main", coordinate_space: "normalized", points: points.map(([x, y]) => ({ x, y })) };
}

function polygonArea(points: NormalizedRoiPoint[]) {
  let area = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % points.length];
    area += x1 * y2 - x2 * y1;
  }
  return Math.abs(area) / 2;
}

function validDraft(points: NormalizedRoiPoint[]) {
  return points.length >= 3 && points.length <= 12 && polygonArea(points) >= 0.0001;
}

function acceptedSave(value: unknown): value is SaveResponse {
  return (
    typeof value === "object" && value !== null && (value as SaveResponse).saved === true &&
    typeof (value as SaveResponse).applied_to_monitor === "boolean" &&
    isDrawableVehicleRoi((value as SaveResponse).zone, "main")
  );
}

function motionLabel(runtime: WagonArchCameraRuntime | null) {
  const motion = runtime?.motion;
  if (!motion || motion.state === "unknown") return "Нет данных о движении";
  if (motion.state === "moving") return "Вагон едет";
  return `Вагон стоит · ${Math.round(motion.still_seconds)} с`;
}

function collectorLabel(runtime: WagonArchCameraRuntime | null) {
  const collector = runtime?.runtime.collector;
  if (!collector) return "Сборщик: нет данных";
  const status: Record<string, string> = {
    running: "работает",
    hardware_unavailable: "нет связи с весами",
    camera_unavailable: "нет связи с ПК камер",
    starting: "запускается",
  };
  return `Сборщик: ${status[collector.status] ?? collector.status}${collector.pending ? ` · в очереди ${collector.pending}` : ""}`;
}

export function WagonArchCameraPanel() {
  const canManage = useAuth((state) => Boolean(state.me?.is_superuser));
  const { data: runtime, loading, error, reload, setData } = useApi<WagonArchCameraRuntime>(RUNTIME_URL);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<NormalizedRoiPoint[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [notice, setNotice] = useState<{ message: string; tone: "success" | "warning" } | null>(null);
  const [streamOnline, setStreamOnline] = useState(false);
  useVisiblePolling(reload, POLL_MS, !editing && !saving);

  const zone = runtime?.zone ?? null;
  const overlay = editing ? draftZone(draft) : zone;
  const zoneConfigured = Boolean(zone && isDrawableVehicleRoi(zone, "main"));
  const canSave = validDraft(draft) && !saving;
  const lastStop = runtime?.runtime.last_stop ?? null;

  function startEditing() {
    if (!canManage || !runtime) return;
    const points = normalizeVehicleRoi(runtime.zone.points);
    setDraft(points.length ? points : DEFAULT_ZONE);
    setSaveError("");
    setNotice(null);
    setEditing(true);
  }

  function accept(payload: SaveResponse) {
    if (runtime) setData({ ...runtime, zone: payload.zone });
    setEditing(false);
    setDraft([]);
    setSaveError("");
    setNotice(
      payload.applied_to_monitor
        ? { message: "Зона сохранена. ПК камер применит её в течение пары секунд.", tone: "success" }
        : { message: "Зона сохранена, но монитор пока не подтвердил обновление. Он перечитает зону после восстановления.", tone: "warning" },
    );
    if (payload.applied_to_monitor) showSuccess("Зона арки сохранена");
  }

  async function save() {
    if (!canManage || !runtime || !canSave) return;
    setSaving(true);
    setSaveError("");
    const body = { points: draft.map(([x, y]) => ({ x, y })), enabled: true, source: "main" };
    try {
      const response = await api.put<SaveResponse>(`/cameras/${runtime.camera}/wagon-arch-runtime/`, body, { timeout: 12_000 });
      if (!acceptedSave(response.data)) throw new Error("Некорректный ответ сохранения зоны");
      accept(response.data);
    } catch (cause) {
      const response = (cause as AxiosError<unknown>).response;
      if (response?.status === 503 && acceptedSave(response.data)) accept(response.data);
      else setSaveError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card role="region" aria-label="Зона арки вагонных весов" className="space-y-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Зона арки</h2>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            Вагон встал в зоне и вес устоялся — записывается заезд; поехал — выезд.
          </p>
        </div>
        {canManage && editing ? (
          <div className="flex flex-wrap items-center gap-2" aria-label="Действия редактора зоны">
            <Button variant="outline" disabled={saving} onClick={() => { setEditing(false); setDraft([]); setSaveError(""); }}>
              <X className="size-4" /> Отмена
            </Button>
            <Button disabled={!canSave} onClick={() => void save()}>
              <Check className="size-4" /> {saving ? "Сохранение…" : "Сохранить зону"}
            </Button>
          </div>
        ) : canManage ? (
          <Button variant="outline" disabled={!runtime || loading || Boolean(error)} onClick={startEditing}>
            <PencilLine className="size-4" /> Изменить зону
          </Button>
        ) : null}
      </div>
      {error && <ErrorAlert message={error} onRetry={() => void reload()} />}
      {runtime?.diagnostic ? (
        <p role="alert" className="rounded-md border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-3 py-2 text-sm">{runtime.diagnostic}</p>
      ) : null}
      {editing && (
        <p role="status" className="text-sm text-[var(--muted-foreground)]">
          Перетащите точки мышью или выберите точку клавишей Tab и двигайте стрелками. Shift + стрелка — крупный шаг.
        </p>
      )}
      {saveError && <p role="alert" className="text-sm text-[var(--destructive)]">{saveError}</p>}
      {notice && (
        <p role="status" className={cn("text-sm", notice.tone === "success" ? "text-[var(--success)]" : "text-[var(--warning)]")}>
          {notice.message}
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-[1.55fr_0.85fr]">
        <div className="relative aspect-video overflow-hidden rounded-lg bg-[#141416]">
          {runtime?.stream ? (
            <CameraStream key={runtime.stream} src={runtime.stream} onStateChange={setStreamOnline} className="absolute inset-0 size-full object-contain" />
          ) : null}
          <VehicleRoiOverlay
            roi={overlay}
            expectedSource="main"
            editable={editing}
            onPointsChange={setDraft}
            label="ЗОНА АРКИ"
            editorLabel="Редактор зоны арки"
            pointLabel="Точка зоны"
          />
          {!streamOnline && !editing && (
            <span className="absolute left-3 top-3 z-[2] text-[11px] text-white/70">Подключаем видеопоток…</span>
          )}
        </div>
        <dl className="grid content-start gap-3 text-sm">
          <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-2">
            <dt className="text-[var(--muted-foreground)]">Зона</dt>
            <dd className="font-medium">{zoneConfigured ? "Задана" : "Зона арки не задана"}</dd>
          </div>
          <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-2">
            <dt className="text-[var(--muted-foreground)]">Движение</dt>
            <dd className="font-medium">{motionLabel(runtime)}</dd>
          </div>
          <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-2">
            <dt className="text-[var(--muted-foreground)]">Контур</dt>
            <dd className="font-medium">{collectorLabel(runtime)}</dd>
          </div>
          <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-2">
            <dt className="text-[var(--muted-foreground)]">Автоматика</dt>
            <dd className="font-medium">{runtime?.automation_enabled ? "включена" : "выключена"}</dd>
          </div>
          {lastStop && (
            <div className="py-2">
              <dt className="text-[var(--muted-foreground)]">Последняя стоянка</dt>
              <dd className="mt-1">
                <span className="font-medium">{lastStop.number ? `Вагон ${lastStop.number}` : "Вагон без номера"}</span>{" "}
                <span className="tabular-nums">{formatKg(lastStop.full_weight_kg)}</span>
                {lastStop.exit_weight_kg != null && <span className="tabular-nums"> → {formatKg(lastStop.exit_weight_kg)}</span>}
                {lastStop.blocked_reason && (
                  <p className="mt-1 text-[var(--warning)]">{archStopReasonLabel(lastStop.blocked_reason)}</p>
                )}
              </dd>
            </div>
          )}
        </dl>
      </div>
    </Card>
  );
}
```

Mount it in `wagon-number-camera.tsx`: import `WagonArchCameraPanel` and render `<WagonArchCameraPanel />` right after `<CameraPanel camera={…} settings={settings} />` inside `WagonNumberCameraWorkspace` (wrap both in a `space-y-6` container if the workspace returns a fragment).

- [ ] **Step 4: Run the tests and typecheck**

Run: `cd frontend && npx vitest run src/components/grain/wagon-arch-camera.test.tsx src/components/grain/wagon-number-camera.test.tsx && npx tsc --noEmit`
Expected: PASS, tsc clean. (`VehicleRoiOverlay` renders `data-testid="vehicle-roi-polygon"` only when `isDrawableVehicleRoi(roi, expectedSource)` is true — the fixture zone satisfies it.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/grain/wagon-arch-camera.tsx frontend/src/components/grain/wagon-arch-camera.test.tsx frontend/src/components/grain/wagon-number-camera.tsx frontend/src/components/grain/wagon-number-camera.test.tsx
git commit -m "feat(grain-ui): arch zone editor and contour status on the wagon camera tab"
```

---

### Task 4: Stops journal tab «Стоянки под аркой»

**Files:**
- Create: `frontend/src/components/grain/wagon-arch-stops.tsx`
- Modify: `frontend/src/components/grain/grain-workspace.tsx:35-48` (`GrainTab`, `INTAKE_TABS`) and the body dispatch ~line 326
- Test: `frontend/src/components/grain/wagon-arch-stops.test.tsx`, `frontend/src/app/grain/page.test.tsx` (extend with the tab)

**Interfaces:**
- Consumes: `GET /grain/wagon-arch/stops/?before=` (Part 3 Task 4), `apiFileUrl`, `formatKg`, `grainTripHref`, `archStopReasonLabel`, `Badge`, `useApi`, `useVisiblePolling`.
- Produces: `export function WagonArchStops()`; `section#wagon-arch-stops[role=tabpanel][aria-labelledby=wagon-arch-stops-tab]`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/grain/wagon-arch-stops.test.tsx`:

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WagonArchStop } from "@/lib/types";
import { WagonArchStops } from "./wagon-arch-stops";

const mocks = vi.hoisted(() => ({
  urls: [] as string[],
  page: { results: [] as WagonArchStop[], next_cursor: null as number | null },
  reload: vi.fn(),
  polling: vi.fn(),
}));
vi.mock("@/lib/use-api", () => ({
  useApi: (url: string) => {
    mocks.urls.push(url);
    return { data: mocks.page, loading: false, error: "", reload: mocks.reload };
  },
}));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: mocks.polling }));
vi.mock("@/lib/api", () => ({ api: { defaults: { baseURL: "https://crm.test/api" } } }));
vi.mock("next/link", () => ({ default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }));

function stop(overrides: Partial<WagonArchStop> = {}): WagonArchStop {
  return {
    id: 7, stop_id: "stop-1", camera: "cam8", arrived_at: "2026-09-14T04:00:00Z", full_weight_kg: 62340,
    exit_weight_kg: 24120, net_kg: 38220, number: "28055531", number_source: "model", recognition_error: "", ocr_attempts: 1,
    status: "closed", blocked_reason: "", blocked_detail: "", motion_gap: false, departed_at: "2026-09-14T04:40:00Z",
    entry_applied_at: "2026-09-14T04:00:10Z", exit_applied_at: "2026-09-14T04:50:00Z", wagon_id: 124, wagon_status: "completed",
    photo_url: "/api/grain/photos/evidence/9/?token=abc", ...overrides,
  };
}

beforeEach(() => {
  mocks.urls = [];
  mocks.page = {
    results: [
      stop(),
      stop({ id: 6, stop_id: "stop-0", number: "", status: "open", blocked_reason: "silo_required", exit_weight_kg: null, net_kg: null, wagon_id: 123, wagon_status: "arrived", photo_url: null }),
      stop({ id: 5, stop_id: "stop-x", status: "attention", blocked_reason: "exit_not_lower", exit_weight_kg: 70000, net_kg: -7660 }),
    ],
    next_cursor: 5,
  };
});

describe("WagonArchStops", () => {
  it("lists stops newest first with weights, status, reason and the trip link", () => {
    render(<WagonArchStops />);
    expect(mocks.urls).toContain("/grain/wagon-arch/stops/");
    expect(mocks.polling).toHaveBeenLastCalledWith(mocks.reload, 5000, true);
    const [first, second, third] = screen.getAllByRole("listitem");
    expect(first).toHaveTextContent("Вагон 28055531");
    expect(first).toHaveTextContent("62 340 кг → 24 120 кг");
    expect(first).toHaveTextContent("нетто 38 220 кг");
    expect(within(first).getByText("Завершена")).toBeInTheDocument();
    expect(within(first).getByRole("link", { name: "Открыть рейс" })).toHaveAttribute("href", "/grain/wagons/124");
    expect(within(first).getByRole("img", { name: "Стоянка 7" })).toHaveAttribute("src", "https://crm.test/api/grain/photos/evidence/9/?token=abc");
    expect(second).toHaveTextContent("Номер не распознан");
    expect(within(second).getByText("Ждёт")).toBeInTheDocument();
    expect(second).toHaveTextContent("Назначьте силос в рейсе — заезд запишется автоматически");
    expect(within(third).getByText("Нужна проверка")).toBeInTheDocument();
    expect(third).toHaveTextContent("Вес на выезде не меньше веса на въезде");
  });

  it("pages to older stops and stops live polling there", async () => {
    const user = userEvent.setup();
    render(<WagonArchStops />);
    await user.click(screen.getByRole("button", { name: "Более ранние" }));
    expect(mocks.urls).toContain("/grain/wagon-arch/stops/?before=5");
    expect(mocks.polling).toHaveBeenLastCalledWith(mocks.reload, 5000, false);
  });

  it("explains an empty journal", () => {
    mocks.page = { results: [], next_cursor: null };
    render(<WagonArchStops />);
    expect(screen.getByText("Стоянок под аркой пока нет")).toBeInTheDocument();
  });
});
```

Extend `frontend/src/app/grain/page.test.tsx` (intake page): assert that the tab `Стоянки под аркой` exists and clicking it renders `role="tabpanel"` with `aria-label`/id `wagon-arch-stops` (mock `./wagon-arch-stops` or provide the stops URL in that test's `useApi` mock — follow how that file already mocks `useApi` for other tabs).

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/components/grain/wagon-arch-stops.test.tsx src/app/grain/page.test.tsx`
Expected: FAIL (`Failed to resolve import "./wagon-arch-stops"`; tab not found).

- [ ] **Step 3: Implement the journal and the tab**

Create `frontend/src/components/grain/wagon-arch-stops.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { apiFileUrl, formatKg, grainTripHref } from "@/lib/grain";
import type { WagonArchStop } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { formatDateTime } from "@/lib/utils";
import { archStopReasonLabel } from "@/lib/weighing-evidence";

const STATUS: Record<WagonArchStop["status"], { label: string; tone: "muted" | "success" | "warning" | "destructive" }> = {
  open: { label: "Идёт", tone: "success" },
  closed: { label: "Завершена", tone: "muted" },
  attention: { label: "Нужна проверка", tone: "destructive" },
  superseded: { label: "Перестановка", tone: "muted" },
};

export function WagonArchStops() {
  const [before, setBefore] = useState<number | null>(null);
  const { data, loading, error, reload } = useApi<{ results: WagonArchStop[]; next_cursor: number | null }>(
    `/grain/wagon-arch/stops/${before ? `?before=${before}` : ""}`,
  );
  useVisiblePolling(reload, 5000, before === null);
  return (
    <section id="wagon-arch-stops" role="tabpanel" aria-labelledby="wagon-arch-stops-tab" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">Стоянки под аркой</h2>
          <p className="text-sm text-[var(--muted-foreground)]">Каждая стоянка вагона: полный вес, пустой вес и рейс.</p>
        </div>
        <Button variant="ghost" size="sm" disabled={loading} onClick={() => void reload()}>
          <RefreshCw className="size-4" /> Обновить
        </Button>
      </div>
      {error && <ErrorAlert message={error} onRetry={() => void reload()} />}
      {data?.results.length === 0 && (
        <p className="rounded-lg bg-[var(--muted)]/35 px-4 py-6 text-center text-sm text-[var(--muted-foreground)]">
          Стоянок под аркой пока нет
        </p>
      )}
      <ul className="divide-y divide-[var(--border)] rounded-lg border">
        {(data?.results ?? []).map((row) => {
          const status = STATUS[row.status] ?? STATUS.open;
          const photo = apiFileUrl(row.photo_url);
          const blocked = row.status === "open" && row.blocked_reason ? "Ждёт" : null;
          return (
            <li key={row.id} className="flex flex-wrap items-start gap-4 px-4 py-3">
              {photo ? (
                <a href={photo} target="_blank" rel="noreferrer">
                  <img src={photo} alt={`Стоянка ${row.id}`} loading="lazy" className="h-14 w-24 rounded object-cover" />
                </a>
              ) : (
                <span className="flex h-14 w-24 items-center justify-center rounded bg-[var(--muted)] text-xs text-[var(--muted-foreground)]">нет кадра</span>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{row.number ? `Вагон ${row.number}` : "Номер не распознан"}</span>
                  <Badge tone={blocked ? "warning" : status.tone}>{blocked ?? status.label}</Badge>
                  <span className="text-xs text-[var(--muted-foreground)]">{formatDateTime(row.arrived_at)}</span>
                </div>
                <p className="mt-1 text-sm tabular-nums">
                  {formatKg(row.full_weight_kg)}
                  {row.exit_weight_kg != null && ` → ${formatKg(row.exit_weight_kg)}`}
                  {row.net_kg != null && row.net_kg > 0 && ` · нетто ${formatKg(row.net_kg)}`}
                </p>
                {row.blocked_reason && (
                  <p className="mt-1 text-sm text-[var(--warning)]">{archStopReasonLabel(row.blocked_reason, row.blocked_detail)}</p>
                )}
              </div>
              {row.wagon_id != null && (
                <Link href={grainTripHref({ id: row.wagon_id, direction: "intake" })} className="text-sm underline">
                  Открыть рейс
                </Link>
              )}
            </li>
          );
        })}
      </ul>
      <div className="flex justify-between">
        <Button variant="outline" size="sm" disabled={before === null} onClick={() => setBefore(null)}>Последние</Button>
        <Button variant="outline" size="sm" disabled={!data?.next_cursor} onClick={() => setBefore(data?.next_cursor ?? null)}>Более ранние</Button>
      </div>
    </section>
  );
}
```

`grain-workspace.tsx`: `type GrainTab = "expected" | "on_site" | "finished" | "history" | "camera" | "arch";`, add `{ key: "arch", label: "Стоянки под аркой", icon: Scale, panelId: "wagon-arch-stops" }` to `INTAKE_TABS` before `camera`, and in the body dispatch add `tab === "arch" ? <WagonArchStops /> :` before the `camera` branch. Check the `list` helpers that treat a tab as a list tab (`listTab`) exclude `"arch"` the same way they exclude `"history"`.

- [ ] **Step 4: Run the tests, full check and build**

Run: `cd frontend && npx vitest run src/components/grain/wagon-arch-stops.test.tsx src/app/grain/page.test.tsx && npm run check && npm run build`
Expected: all PASS; build clean (`/grain` route unchanged in size class).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/grain/wagon-arch-stops.tsx frontend/src/components/grain/wagon-arch-stops.test.tsx frontend/src/components/grain/grain-workspace.tsx frontend/src/app/grain/page.test.tsx
git commit -m "feat(grain-ui): «Стоянки под аркой» journal tab for intake"
```

---

## Rollout

Deploys with the normal pipeline. On the plant: open «Приход → Камера проходной», press «Изменить зону», drag the four points over the wagon body under the arch (keep the camera OSD clock outside), «Сохранить зону». The status block must show «Вагон стоит/едет» within a few seconds once Part 1 runs on the camera PC.

## Self-review

- Spec coverage: zone editor on cam8 with save to the camera PC (Task 1, 3), contour status (scale — Task 2 toolbar; camera motion, collector, last stop — Task 3), stops journal with pending stops and their reason + action (Task 4). ✔
- Type consistency: `WagonArchCameraRuntime` (Task 2) ↔ view payload (Task 1) ↔ panel (Task 3); `WagonArchStop` (Task 2) ↔ serializer fields (Part 3 Task 4) ↔ journal (Task 4); `archStopReasonLabel` codes ↔ `blocked_reason` values written in Part 3. ✔
- No placeholders. ✔
