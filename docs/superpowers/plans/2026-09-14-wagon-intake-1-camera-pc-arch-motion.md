# Wagon Intake · Part 1 — Camera-PC arch zone and motion state

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The camera-PC service reports, for the wagon-scale camera, whether the unloading arch zone is `moving` or `still` (and for how long), and lets the CRM store the zone polygon — so the wagon-scale collector (Part 2) can decide when a wagon has stopped under the arch.

**Architecture:** Reuse the existing per-camera polygon storage (`VehicleRoiRepository`, a second JSON file) and the existing monitor pattern (`VehiclePlateAutoMonitor`: capture lease + clock + logger + thread). Motion is measured by dense Farnebäck optical flow inside the zone on a small min-max-normalized grayscale sample; the state machine needs consecutive samples to flip, so single glitches, light changes and sensor noise do not count as a wagon moving. Everything is exposed through the existing HTTP facade/handler.

**Tech Stack:** Python 3.12, OpenCV (`opencv-python`, injected as `cv2_module`), NumPy, stdlib threading, pytest. Windows deployment via `deploy/camera-pc/install-ai-service.ps1` → settings JSON → `service_launcher.py` → `AI_*` env.

**Spec:** `docs/superpowers/specs/2026-09-12-wagon-intake-arch-design.md` (section «1. ПК камер»).

**Repository for all code in this plan:** `/Users/dimash/PycharmProjects/bag-counter-cv-service` (NOT asyl-ltd). Paths below are relative to that repo unless prefixed. Run tests from `cv_service/`:

```bash
cd /Users/dimash/PycharmProjects/bag-counter-cv-service/cv_service && ../.venv/bin/python -m pytest tests -q
```

Lint (CI runs it): `cd cv_service && ../.venv/bin/python -m ruff check --select E4,E7,E9,F src tests` (skip if ruff is not installed locally; CI will run it).

## Global Constraints

- New HTTP actions live under the existing 3-segment camera route: `GET/PUT /cameras/<cam>/arch-zone`, `GET /cameras/<cam>/arch-motion`. `parse_route` already yields `Route("cameras", camera, action)` for them — no routing change.
- Zone polygon contract is identical to `vehicle-roi`: 3–12 normalized points, `[x, y]` or `{x, y}`, area ≥ 0.0001 (`src.domain.vehicle_plate.normalize_polygon`).
- Motion defaults (validated on synthetic frames on 2026-09-14): sample width 160 px, `min_flow_px = 1.0`, `moving_fraction = 0.15`, `moving_samples = 2`, `still_samples = 3`, `fps = 4.0`. A 12 px shift of a 640 px frame gives moving_fraction ≈ 0.97; the same frame, a 45 % darker frame and ±6 sensor noise give 0.0; a 2 px jitter gives 0.0 at `min_flow_px = 1.0`; a person-sized 40×130 px patch gives ≈ 0.065.
- `cv2` is never imported at module scope in `src/`; it is injected (`OpenCvImageOperations(cv2_module)`). Tests that need real OpenCV import it locally inside the test function (existing convention in `tests/infrastructure/vision/test_opencv_frames.py`).
- Every new `AI_*` env name must be added to `ACTIVE_ENV_NAMES` in `cv_service/tests/config/test_loader.py`, documented in `cv_service/.env.example`, and exported by `deploy/camera-pc/ai/service_launcher.py`.
- The feature is off by default (`AI_ARCH_MOTION_ENABLED=false`); enabling it on the plant PC is `install-ai-service.ps1 -ArchMotionCameras cam8` (Task 6).

---

## File structure

| File | Responsibility |
|---|---|
| `cv_service/src/config/models.py` | `ArchSettings` dataclass; `StorageSettings.arch_zones_path`; `AppSettings.arch` |
| `cv_service/src/config/loader.py` | `AI_ARCH_MOTION_*` / `AI_ARCH_ZONES_PATH` parsing |
| `cv_service/src/config/__init__.py` | export `ArchSettings` |
| `cv_service/src/infrastructure/persistence/json_repositories.py` | `VehicleRoiRepository(label=…)` so the same class stores arch zones with honest error messages |
| `cv_service/src/infrastructure/vision/opencv_frames.py` | `arch_zone_sample`, `arch_zone_flow` (the only OpenCV code) |
| `cv_service/src/application/automation/settings.py` | `ArchMonitorSettings` |
| `cv_service/src/application/automation/arch_motion_monitor.py` | `ArchMotionMonitor` thread + state machine (no OpenCV; sampler/flow injected) |
| `cv_service/src/application/automation/__init__.py` | exports |
| `cv_service/src/application/http_contracts.py`, `http_facade.py` | `arch_zone`, `update_arch_zone`, `arch_motion`; health block |
| `cv_service/src/presentation/http/handler.py` | GET/PUT dispatch |
| `cv_service/src/composition/automation.py`, `service.py` | build monitors, wire repository/refresher/status |
| `deploy/camera-pc/ai/service_launcher.py`, `deploy/camera-pc/install-ai-service.ps1` | settings JSON → env; installer parameter |
| `cv_service/.env.example`, `cv_service/AI_SERVICE.md` | docs |

---

### Task 1: Configuration surface (`ArchSettings`, storage path, env, launcher)

**Files:**
- Modify: `cv_service/src/config/models.py` (`StorageSettings` ~line 317, `AppSettings` ~line 337; add `ArchSettings` after `VehicleSettings`)
- Modify: `cv_service/src/config/loader.py` (after the `vehicle = VehicleSettings(...)` block; `storage = StorageSettings(...)` ~line 1102; `return AppSettings(...)` ~line 1147)
- Modify: `cv_service/src/config/__init__.py`
- Modify: `cv_service/.env.example` (after the `AI_VEHICLE_CONFIRMATION_WINDOW_SECONDS` line ~111; and after `AI_VEHICLE_ROIS_PATH` ~123)
- Modify: `deploy/camera-pc/ai/service_launcher.py` (env dict, next to `"AI_VEHICLE_ROIS_PATH"` ~line 636)
- Test: `cv_service/tests/config/test_loader.py`, `cv_service/tests/test_service_launcher.py`

**Interfaces:**
- Produces: `AppSettings.arch: ArchSettings` with fields `enabled: bool, cameras: tuple[str, ...], source: StreamSource, fps: float, moving_fraction: float, min_flow_px: float, moving_samples: int, still_samples: int`; `StorageSettings.arch_zones_path: Path`.
- Env names: `AI_ARCH_MOTION_ENABLED`, `AI_ARCH_MOTION_CAMERAS`, `AI_ARCH_MOTION_SOURCE`, `AI_ARCH_MOTION_FPS`, `AI_ARCH_MOTION_MOVING_FRACTION`, `AI_ARCH_MOTION_MIN_FLOW_PX`, `AI_ARCH_MOTION_MOVING_SAMPLES`, `AI_ARCH_MOTION_STILL_SAMPLES`, `AI_ARCH_ZONES_PATH`.
- Launcher settings-JSON keys: `arch_motion_enabled`, `arch_motion_cameras`, `arch_motion_source`, `arch_motion_fps`, `arch_motion_moving_fraction`, `arch_motion_min_flow_px`, `arch_motion_moving_samples`, `arch_motion_still_samples`.

- [ ] **Step 1: Write the failing loader test**

Append to `cv_service/tests/config/test_loader.py`:

```python
def test_arch_motion_settings_are_off_by_default_and_normalized(tmp_path):
    settings = load_settings({}, tmp_path)
    assert settings.arch.enabled is False
    assert settings.arch.cameras == ()
    assert settings.arch.source == "main"
    assert (settings.arch.fps, settings.arch.moving_fraction, settings.arch.min_flow_px) == (4.0, 0.15, 1.0)
    assert (settings.arch.moving_samples, settings.arch.still_samples) == (2, 3)
    assert settings.storage.arch_zones_path == (tmp_path / "arch-zones.json").resolve()

    settings = load_settings(
        {
            "AI_ARCH_MOTION_ENABLED": "true",
            "AI_ARCH_MOTION_CAMERAS": "CAM8, cam8,cam7",
            "AI_ARCH_MOTION_SOURCE": "SUB",
            "AI_ARCH_MOTION_FPS": "6",
            "AI_ARCH_MOTION_MOVING_FRACTION": "0.3",
            "AI_ARCH_MOTION_MIN_FLOW_PX": "0.8",
            "AI_ARCH_MOTION_MOVING_SAMPLES": "3",
            "AI_ARCH_MOTION_STILL_SAMPLES": "5",
            "AI_ARCH_ZONES_PATH": "zones/arch.json",
        },
        tmp_path,
    )
    assert settings.arch.enabled is True
    assert settings.arch.cameras == ("cam8", "cam7")
    assert settings.arch.source == "sub"
    assert (settings.arch.fps, settings.arch.moving_fraction, settings.arch.min_flow_px) == (6.0, 0.3, 0.8)
    assert (settings.arch.moving_samples, settings.arch.still_samples) == (3, 5)
    assert settings.storage.arch_zones_path == (tmp_path / "zones" / "arch.json").resolve()


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"AI_ARCH_MOTION_CAMERAS": "cam8,front"}, "AI_ARCH_MOTION_CAMERAS values must match cam<N>"),
        ({"AI_ARCH_MOTION_SOURCE": "rtsp"}, "AI_ARCH_MOTION_SOURCE must be main or sub"),
        ({"AI_ARCH_MOTION_FPS": "0.1"}, "AI_ARCH_MOTION_FPS must be a finite number >= 0.5"),
        ({"AI_ARCH_MOTION_MOVING_FRACTION": "1.5"}, "AI_ARCH_MOTION_MOVING_FRACTION must be at most 1"),
        ({"AI_ARCH_MOTION_MOVING_SAMPLES": "0"}, "AI_ARCH_MOTION_MOVING_SAMPLES must be >= 1"),
    ],
)
def test_arch_motion_settings_reject_invalid_values(tmp_path, env, message):
    with pytest.raises(RuntimeError, match=re.escape(message)):
        load_settings(env, tmp_path)
```

Also add the nine env names to `ACTIVE_ENV_NAMES` (same file, the set near the top — insert anywhere, e.g. after `"AI_ALWAYS_ON_PATH"`):

```python
    "AI_ARCH_MOTION_ENABLED",
    "AI_ARCH_MOTION_CAMERAS",
    "AI_ARCH_MOTION_SOURCE",
    "AI_ARCH_MOTION_FPS",
    "AI_ARCH_MOTION_MOVING_FRACTION",
    "AI_ARCH_MOTION_MIN_FLOW_PX",
    "AI_ARCH_MOTION_MOVING_SAMPLES",
    "AI_ARCH_MOTION_STILL_SAMPLES",
    "AI_ARCH_ZONES_PATH",
```

- [ ] **Step 2: Run the loader tests to verify they fail**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/config/test_loader.py -q -k "arch_motion or every_effective_environment_name"`
Expected: FAIL — `AttributeError: 'AppSettings' object has no attribute 'arch'` for the new tests; `test_config_layer_exposes_every_effective_environment_name` fails because the loader does not read the new names yet.

- [ ] **Step 3: Add `ArchSettings`, the storage path and the `AppSettings` field**

In `cv_service/src/config/models.py`, after the `VehicleSettings` dataclass:

```python
@dataclass(frozen=True, slots=True)
class ArchSettings:
    """Motion watch over the unloading arch of the wagon scale.

    ``moving_fraction`` is the share of the zone that must be displaced by at
    least ``min_flow_px`` (at the 160 px sample width) for one sample to count
    as moving; ``moving_samples`` / ``still_samples`` consecutive samples flip
    the reported state.
    """

    enabled: bool
    cameras: tuple[str, ...]
    source: StreamSource
    fps: float
    moving_fraction: float
    min_flow_px: float
    moving_samples: int
    still_samples: int
```

In `StorageSettings` add `arch_zones_path: Path` right after `vehicle_rois_path: Path`. In `AppSettings` add `arch: ArchSettings` right after `vehicle: VehicleSettings`.

- [ ] **Step 4: Parse the env in the loader and export the class**

In `cv_service/src/config/loader.py`, immediately after the statement `vehicle = VehicleSettings(...)` closes (before the next settings block), add:

```python
    arch_cameras = _validated_cameras(
        _unique_csv(env.get("AI_ARCH_MOTION_CAMERAS", ""), lower=True),
        "AI_ARCH_MOTION_CAMERAS",
    )
    arch_moving_fraction = _env_float(env, "AI_ARCH_MOTION_MOVING_FRACTION", 0.15, 0.01)
    if arch_moving_fraction > 1.0:
        raise RuntimeError("AI_ARCH_MOTION_MOVING_FRACTION must be at most 1")
    arch = ArchSettings(
        enabled=_env_bool(env, "AI_ARCH_MOTION_ENABLED", False),
        cameras=arch_cameras,
        source=_source(
            env.get("AI_ARCH_MOTION_SOURCE", "main").strip().lower(),
            "AI_ARCH_MOTION_SOURCE",
        ),
        fps=_env_float(env, "AI_ARCH_MOTION_FPS", 4.0, 0.5),
        moving_fraction=arch_moving_fraction,
        min_flow_px=_env_float(env, "AI_ARCH_MOTION_MIN_FLOW_PX", 1.0, 0.1),
        moving_samples=_env_int(env, "AI_ARCH_MOTION_MOVING_SAMPLES", 2, 1),
        still_samples=_env_int(env, "AI_ARCH_MOTION_STILL_SAMPLES", 3, 1),
    )
```

In the `storage = StorageSettings(...)` call add, after `vehicle_rois_path=...`:

```python
        arch_zones_path=_rooted_storage_path(
            env, root, "AI_ARCH_ZONES_PATH", str(root / "arch-zones.json")
        ),
```

In `return AppSettings(...)` add `arch=arch,` after `vehicle=vehicle,`. Add `ArchSettings` to the `from .models import (...)` list at the top of `loader.py`.

In `cv_service/src/config/__init__.py` add `ArchSettings` to the import list from `.models` and to `__all__` (alphabetical, after `AppSettings`).

- [ ] **Step 5: Run the loader tests**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/config -q`
Expected: PASS. If `test_default_settings_match_monolith_defaults` enumerates dataclass fields or a repr and fails, extend its expectation with the `arch` block using the defaults from Step 1 (`enabled=False, cameras=(), source="main", fps=4.0, moving_fraction=0.15, min_flow_px=1.0, moving_samples=2, still_samples=3`) — nothing else.

- [ ] **Step 6: Write the failing launcher test**

Append to `cv_service/tests/test_service_launcher.py`:

```python
def test_launcher_exports_arch_motion_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _launcher()
    monkeypatch.setattr(launcher.os, "environ", {})
    monkeypatch.setattr(launcher, "mediamtx_users", lambda _path: {})

    launcher.configure_environment(
        {
            "media_root": str(tmp_path / "media"),
            "api_key_sha256": "a" * 64,
            "model_path": str(tmp_path / "models/detector.pt"),
            "arch_motion_enabled": True,
            "arch_motion_cameras": "cam8",
            "arch_motion_fps": 5,
            "arch_motion_moving_fraction": 0.2,
        }
    )

    environ = launcher.os.environ
    assert environ["AI_ARCH_MOTION_ENABLED"] == "true"
    assert environ["AI_ARCH_MOTION_CAMERAS"] == "cam8"
    assert environ["AI_ARCH_MOTION_SOURCE"] == "main"
    assert environ["AI_ARCH_MOTION_FPS"] == "5"
    assert environ["AI_ARCH_MOTION_MOVING_FRACTION"] == "0.2"
    assert environ["AI_ARCH_MOTION_MIN_FLOW_PX"] == "1.0"
    assert environ["AI_ARCH_MOTION_MOVING_SAMPLES"] == "2"
    assert environ["AI_ARCH_MOTION_STILL_SAMPLES"] == "3"
    assert environ["AI_ARCH_ZONES_PATH"] == str(launcher.ROOT / "arch-zones.json")
```

- [ ] **Step 7: Run it to verify it fails**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/test_service_launcher.py -q -k arch_motion`
Expected: FAIL with `KeyError: 'AI_ARCH_MOTION_ENABLED'`.

- [ ] **Step 8: Export the env from the launcher and document it**

In `deploy/camera-pc/ai/service_launcher.py`, inside the env dict, right after the `"AI_VEHICLE_ROIS_PATH": str(ROOT / "vehicle-rois.json"),` entry add:

```python
        "AI_ARCH_MOTION_ENABLED": str(
            settings.get("arch_motion_enabled", False)
        ).lower(),
        "AI_ARCH_MOTION_CAMERAS": settings.get("arch_motion_cameras", ""),
        "AI_ARCH_MOTION_SOURCE": settings.get("arch_motion_source", "main"),
        "AI_ARCH_MOTION_FPS": str(settings.get("arch_motion_fps", 4.0)),
        "AI_ARCH_MOTION_MOVING_FRACTION": str(
            settings.get("arch_motion_moving_fraction", 0.15)
        ),
        "AI_ARCH_MOTION_MIN_FLOW_PX": str(settings.get("arch_motion_min_flow_px", 1.0)),
        "AI_ARCH_MOTION_MOVING_SAMPLES": str(
            settings.get("arch_motion_moving_samples", 2)
        ),
        "AI_ARCH_MOTION_STILL_SAMPLES": str(
            settings.get("arch_motion_still_samples", 3)
        ),
        "AI_ARCH_ZONES_PATH": str(ROOT / "arch-zones.json"),
```

In `cv_service/.env.example`, after the `AI_VEHICLE_CONFIRMATION_WINDOW_SECONDS=10` line add:

```
# Motion watch over the wagon-scale unloading arch (asyl-ltd wagon collector
# polls GET /cameras/<cam>/arch-motion). The zone polygon is saved through
# PUT /cameras/<cam>/arch-zone into AI_ARCH_ZONES_PATH.
AI_ARCH_MOTION_ENABLED=false
AI_ARCH_MOTION_CAMERAS=
AI_ARCH_MOTION_SOURCE=main
AI_ARCH_MOTION_FPS=4
AI_ARCH_MOTION_MOVING_FRACTION=0.15
AI_ARCH_MOTION_MIN_FLOW_PX=1.0
AI_ARCH_MOTION_MOVING_SAMPLES=2
AI_ARCH_MOTION_STILL_SAMPLES=3
```

and after the `AI_VEHICLE_ROIS_PATH=vehicle-rois.json` line add `AI_ARCH_ZONES_PATH=arch-zones.json`.

- [ ] **Step 9: Run the launcher and loader tests**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/test_service_launcher.py tests/config -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
cd /Users/dimash/PycharmProjects/bag-counter-cv-service
git add cv_service/src/config/models.py cv_service/src/config/loader.py cv_service/src/config/__init__.py cv_service/.env.example deploy/camera-pc/ai/service_launcher.py cv_service/tests/config/test_loader.py cv_service/tests/test_service_launcher.py
git commit -m "feat(config): arch-motion settings and arch-zones storage path"
```

---

### Task 2: Arch-zone storage and `GET/PUT /cameras/<cam>/arch-zone`

**Files:**
- Modify: `cv_service/src/infrastructure/persistence/json_repositories.py:164-244` (`VehicleRoiRepository`)
- Modify: `cv_service/src/application/http_contracts.py` (`HttpApplication` Protocol, after `update_vehicle_roi`)
- Modify: `cv_service/src/application/http_facade.py` (`AiHttpApplication.__init__` ~197; new methods after `update_vehicle_roi` ~1136)
- Modify: `cv_service/src/presentation/http/handler.py` (GET block ~535; PUT block after the `vehicle-roi` PUT ~858)
- Modify: `cv_service/src/composition/service.py` (~651 repository construction; ~819 `AiHttpApplication(...)` kwargs)
- Test: `cv_service/tests/infrastructure/test_json_repositories.py`, `cv_service/tests/application/test_http_facade.py`, `cv_service/tests/presentation/http/test_handler.py`

**Interfaces:**
- Consumes: `settings.storage.arch_zones_path`, `settings.arch.source` (Task 1).
- Produces: `AiHttpApplication(arch_zone_repository=..., arch_default_source=..., arch_zone_refresher=...)`; methods `arch_zone(camera) -> HttpResult`, `update_arch_zone(camera, *, points, enabled, source) -> HttpResult`. Response body of `GET arch-zone` is exactly the `vehicle-roi` shape: `{cam, configured, enabled, source, coordinate_space: "normalized", points: [{x, y}], updated_at}`. `PUT` returns `{ok, saved, applied_to_monitor, **zone}` (200), or 503 with `saved: true, applied_to_monitor: false` when the refresher raises.

- [ ] **Step 1: Write the failing repository test**

Append to `cv_service/tests/infrastructure/test_json_repositories.py`:

```python
def test_vehicle_roi_repository_label_names_the_zone_kind_in_errors(tmp_path):
    path = tmp_path / "arch-zones.json"
    path.write_text("[]", encoding="utf-8")
    repository = VehicleRoiRepository(path, label="arch zone", now=fixed_now)

    with pytest.raises(RuntimeError, match="arch zone settings must contain a cameras object"):
        repository.get("cam8")

    path.unlink()
    saved = repository.save("cam8", [[0.1, 0.3], [0.9, 0.3], [0.9, 0.7], [0.1, 0.7]])
    assert saved["cam"] == "cam8" and saved["configured"] is True
    assert VehicleRoiRepository(path, now=fixed_now).get("cam8")["points"] == saved["points"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/infrastructure/test_json_repositories.py -q -k label`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'label'`.

- [ ] **Step 3: Add the label**

In `VehicleRoiRepository.__init__` add the keyword `label: str = "vehicle ROI"` after `now=...` and store `self.label = label`. Replace the three hard-coded messages:
- `f"cannot read vehicle ROI settings: {self.path}"` → `f"cannot read {self.label} settings: {self.path}"`
- `"vehicle ROI settings must contain a cameras object"` → `f"{self.label} settings must contain a cameras object"`
- both `f"invalid saved vehicle ROI settings for {camera}..."` → `f"invalid saved {self.label} settings for {camera}..."` (keep the `: {exc}` suffix where present).

- [ ] **Step 4: Run the repository tests**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/infrastructure/test_json_repositories.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing facade tests**

In `cv_service/tests/application/test_http_facade.py`, extend `build_application(...)`: add parameters `arch_zone_refresher=None` and `arch_zone_repository=None`, and pass to `AiHttpApplication(...)`:

```python
        arch_zone_repository=arch_zone_repository,
        arch_default_source="main",
        arch_zone_refresher=(
            arch_zone_refresher
            if arch_zone_refresher is not None
            else lambda camera: camera == "cam8"
        ),
```

Append tests:

```python
def test_arch_zone_is_unavailable_without_a_repository() -> None:
    application, *_rest = build_application()
    assert application.arch_zone("cam8").status == 503
    assert application.update_arch_zone("cam8", points=[], enabled=True, source=None).status == 503


def test_update_arch_zone_uses_default_source_and_refreshes_monitor() -> None:
    zones = RoiRepository()
    application, *_rest = build_application(arch_zone_repository=zones)
    points = [{"x": 0.1, "y": 0.3}, {"x": 0.9, "y": 0.3}, {"x": 0.9, "y": 0.7}, {"x": 0.1, "y": 0.7}]

    result = application.update_arch_zone("cam8", points=points, enabled=True, source=None)

    assert result.status == 200
    assert result.body["source"] == "main"
    assert result.body["applied_to_monitor"] is True
    assert result.body["points"] == points
    assert application.arch_zone("cam8").body["points"] == points


def test_update_arch_zone_reports_saved_state_when_refresh_fails() -> None:
    def fail_refresh(_camera):
        raise RuntimeError("monitor unavailable")

    application, *_rest = build_application(
        arch_zone_repository=RoiRepository(), arch_zone_refresher=fail_refresh
    )
    result = application.update_arch_zone(
        "cam8",
        points=[{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}],
        enabled=True,
        source="main",
    )
    assert result.status == 503
    assert result.body["saved"] is True
    assert result.body["applied_to_monitor"] is False


@pytest.mark.parametrize(
    ("error", "status"),
    [(ValueError("bad polygon"), 400), (RuntimeError("write failed"), 500)],
)
def test_update_arch_zone_maps_repository_errors(error: BaseException, status: int) -> None:
    zones = RoiRepository()
    zones.error = error
    application, *_rest = build_application(arch_zone_repository=zones)
    assert application.update_arch_zone("cam8", points=[], enabled=False, source="sub").status == status
```

(`RoiRepository` is the existing stub class in this test file; check its `save`/`get` handle a second instance the same way — it keeps per-instance state.)

- [ ] **Step 6: Run the facade tests to verify they fail**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/application/test_http_facade.py -q -k arch_zone`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'arch_zone_repository'`.

- [ ] **Step 7: Implement the facade methods and contract**

In `cv_service/src/application/http_facade.py`, `AiHttpApplication.__init__`: add keyword parameters (after `vehicle_default_source: str = "main",`):

```python
        arch_zone_repository: VehicleRoiRepository | None = None,
        arch_default_source: str = "main",
        arch_zone_refresher: RoiRefresher = _not_applied,
```

and store them (`self.arch_zone_repository = arch_zone_repository`, `self.arch_default_source = arch_default_source`, `self.arch_zone_refresher = arch_zone_refresher`).

After `update_vehicle_roi` add:

```python
    def arch_zone(self, camera: str) -> HttpResult:
        preflight = self.camera_mutation_preflight(camera)
        if preflight is not None:
            return preflight
        if self.arch_zone_repository is None:
            return HttpResult(503, {"error": "arch zones are not configured", "cam": camera})
        try:
            return HttpResult.ok(self.arch_zone_repository.get(camera))
        except (RuntimeError, ValueError) as exc:
            return HttpResult(500, {"error": self._error(exc), "cam": camera})

    def update_arch_zone(
        self,
        camera: str,
        *,
        points: Any,
        enabled: bool,
        source: Any | None,
    ) -> HttpResult:
        if self.arch_zone_repository is None:
            return HttpResult(503, {"error": "arch zones are not configured", "cam": camera})
        try:
            saved = self.arch_zone_repository.save(
                camera,
                points,
                enabled=enabled,
                source=str(source if source is not None else self.arch_default_source),
            )
        except ValueError as exc:
            return HttpResult(400, {"error": self._error(exc), "cam": camera})
        except RuntimeError as exc:
            return HttpResult(500, {"error": self._error(exc), "cam": camera})
        try:
            applied = bool(self.arch_zone_refresher(camera))
        except Exception as exc:  # noqa: BLE001 - injected monitor boundary
            error = self._error(exc)
            self._log("arch_zone_refresh_failed", level="warning", cam=camera, error=error)
            return HttpResult(
                503,
                {"error": error, "saved": True, "applied_to_monitor": False, **saved},
            )
        self._log(
            "arch_zone_saved",
            cam=camera,
            enabled=saved["enabled"],
            source=saved["source"],
            points=len(saved["points"]),
            applied_to_monitor=applied,
        )
        return HttpResult.ok(
            {"ok": True, "saved": True, "applied_to_monitor": applied, **saved}
        )
```

In `cv_service/src/application/http_contracts.py`, `HttpApplication` Protocol, after `update_vehicle_roi`:

```python
    def arch_zone(self, camera: str) -> HttpResult: ...

    def update_arch_zone(
        self,
        camera: str,
        *,
        points: Any,
        enabled: bool,
        source: Any | None,
    ) -> HttpResult: ...
```

- [ ] **Step 8: Run the facade tests**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/application/test_http_facade.py -q`
Expected: PASS.

- [ ] **Step 9: Write the failing handler tests**

In `cv_service/tests/presentation/http/test_handler.py`:
- add `("/cameras/cam1/arch-zone", "arch_zone"),` to the `test_get_routes_delegate_to_facade` parametrize list (after the `vehicle-roi` row);
- add `("PUT", "/cameras/cam1/arch-zone", b"{}", "points are required"),` to the body-validation parametrize (~line 1328, next to the `vehicle-roi` row);
- append a delegation test:

```python
def test_put_arch_zone_delegates_after_camera_preflight() -> None:
    application = RecordingApplication()

    invoke(
        "PUT",
        "/cameras/cam8/arch-zone",
        application,
        body=b'{"points":[[0.1,0.3],[0.9,0.3],[0.9,0.7],[0.1,0.7]],"enabled":true}',
        content_type="application/json",
    )

    assert application.calls == [
        ("camera_mutation_preflight", ("cam8",), {}),
        (
            "update_arch_zone",
            ("cam8",),
            {
                "points": [[0.1, 0.3], [0.9, 0.3], [0.9, 0.7], [0.1, 0.7]],
                "enabled": True,
                "source": None,
            },
        ),
    ]
```

- [ ] **Step 10: Run the handler tests to verify they fail**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/presentation/http/test_handler.py -q -k "arch_zone or get_routes_delegate or points_are_required"`
Expected: FAIL — the GET returns 404 `{"error": "not found"}` and the PUT hits the final 404 branch.

- [ ] **Step 11: Dispatch in the handler**

In `do_GET` replace the `line`/`vehicle-roi` block with:

```python
        if route.endpoint == "cameras" and route.action in {"line", "vehicle-roi", "arch-zone"}:
            if not self._valid_camera(route.camera):
                self._send(400, {"error": "camera id must match cam<N>"})
                return
            camera = cast(str, route.camera)
            if route.action == "line":
                self._send_result(application.camera_line(camera))
            elif route.action == "vehicle-roi":
                self._send_result(application.vehicle_roi(camera))
            else:
                self._send_result(application.arch_zone(camera))
            return
```

In `do_PUT`, immediately after the `vehicle-roi` block's `return` (before the final not-found branch) add:

```python
        if (
            route.endpoint == "cameras"
            and route.action == "arch-zone"
            and route.camera is not None
        ):
            if not self._valid_camera(route.camera):
                self._send(400, {"error": "camera id must match cam<N>"})
                return
            preflight = application.camera_mutation_preflight(route.camera)
            if preflight is not None:
                self._send_result(preflight)
                return
            try:
                body = self._body()
                if "points" not in body:
                    raise ValueError("points are required")
            except ValueError as exc:
                self._send(400, {"error": str(exc), "cam": route.camera})
                return
            self._send_result(
                application.update_arch_zone(
                    route.camera,
                    points=body["points"],
                    enabled=bool(body.get("enabled", True)),
                    source=body.get("source"),
                )
            )
            return
```

- [ ] **Step 12: Wire the repository in the composition root**

In `cv_service/src/composition/service.py`, right after `roi_repository = VehicleRoiRepository(...)` add:

```python
        arch_zone_repository = VehicleRoiRepository(
            settings.storage.arch_zones_path,
            default_source=settings.arch.source,
            label="arch zone",
        )
```

and in the `application = AiHttpApplication(...)` call add `arch_zone_repository=arch_zone_repository,` and `arch_default_source=settings.arch.source,` (the refresher is wired in Task 5).

- [ ] **Step 13: Run the handler, facade and composition tests**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/presentation tests/application/test_http_facade.py tests/composition -q`
Expected: PASS.

- [ ] **Step 14: Commit**

```bash
git add cv_service/src/infrastructure/persistence/json_repositories.py cv_service/src/application/http_contracts.py cv_service/src/application/http_facade.py cv_service/src/presentation/http/handler.py cv_service/src/composition/service.py cv_service/tests/infrastructure/test_json_repositories.py cv_service/tests/application/test_http_facade.py cv_service/tests/presentation/http/test_handler.py
git commit -m "feat(http): store and serve the arch zone polygon per camera"
```

---

### Task 3: Optical-flow primitives (`arch_zone_sample`, `arch_zone_flow`)

**Files:**
- Modify: `cv_service/src/infrastructure/vision/opencv_frames.py` (class `OpenCvImageOperations`, after `cargo_scene_motion` ~line 108)
- Test: `cv_service/tests/infrastructure/vision/test_opencv_frames.py`

**Interfaces:**
- Produces: `OpenCvImageOperations.arch_zone_sample(frame, polygon) -> tuple[gray, mask] | None` where `polygon` is a sequence of `(x, y)` normalized tuples (as returned by `normalize_polygon`); `OpenCvImageOperations.arch_zone_flow(previous, current, *, min_flow_px: float) -> {"moving_fraction": float, "dx": float, "dy": float}` (raises `ValueError` when the samples cannot be compared).

- [ ] **Step 1: Write the failing tests**

Append to `cv_service/tests/infrastructure/vision/test_opencv_frames.py`:

```python
ARCH_POLYGON = ((0.1, 0.3), (0.9, 0.3), (0.9, 0.7), (0.1, 0.7))


def _textured_frame(seed: int = 0):
    import cv2
    import numpy as np

    rng = np.random.default_rng(seed)
    frame = rng.integers(0, 256, size=(360, 640, 3), dtype=np.uint8)
    return cv2.GaussianBlur(frame, (7, 7), 0)


def test_arch_zone_flow_reports_a_wagon_shift_but_not_light_or_sensor_noise():
    import cv2
    import numpy as np

    operations = OpenCvImageOperations(cv2)
    frame = _textured_frame()
    still = operations.arch_zone_sample(frame, ARCH_POLYGON)
    shifted = operations.arch_zone_sample(np.roll(frame, 12, axis=1), ARCH_POLYGON)
    jitter = operations.arch_zone_sample(np.roll(frame, 2, axis=1), ARCH_POLYGON)
    darker = operations.arch_zone_sample(
        np.clip(frame.astype(np.int16) * 0.55, 0, 255).astype(np.uint8), ARCH_POLYGON
    )
    noise = np.random.default_rng(1).integers(-6, 7, size=frame.shape)
    noisy = operations.arch_zone_sample(
        np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8), ARCH_POLYGON
    )

    moving = operations.arch_zone_flow(still, shifted, min_flow_px=1.0)
    assert moving["moving_fraction"] > 0.8
    assert moving["dx"] > 1.5
    assert operations.arch_zone_flow(still, still, min_flow_px=1.0)["moving_fraction"] == 0.0
    assert operations.arch_zone_flow(still, jitter, min_flow_px=1.0)["moving_fraction"] < 0.05
    assert operations.arch_zone_flow(still, darker, min_flow_px=1.0)["moving_fraction"] < 0.05
    assert operations.arch_zone_flow(still, noisy, min_flow_px=1.0)["moving_fraction"] < 0.05


def test_arch_zone_flow_ignores_a_person_and_anything_outside_the_polygon():
    import cv2
    import numpy as np

    operations = OpenCvImageOperations(cv2)
    frame = _textured_frame()
    person = frame.copy()
    person[120:250, 300:340] = np.random.default_rng(2).integers(0, 256, size=(130, 40, 3), dtype=np.uint8)
    outside = frame.copy()
    outside[0:100, :, :] = np.roll(outside[0:100, :, :], 12, axis=1)
    still = operations.arch_zone_sample(frame, ARCH_POLYGON)

    person_flow = operations.arch_zone_flow(still, operations.arch_zone_sample(person, ARCH_POLYGON), min_flow_px=1.0)
    assert person_flow["moving_fraction"] < 0.15
    outside_flow = operations.arch_zone_flow(still, operations.arch_zone_sample(outside, ARCH_POLYGON), min_flow_px=1.0)
    assert outside_flow["moving_fraction"] == 0.0


def test_arch_zone_sample_rejects_unusable_frames_and_tiny_zones():
    import cv2
    import numpy as np

    operations = OpenCvImageOperations(cv2)
    assert operations.arch_zone_sample(np.zeros((360, 640, 3), dtype=np.uint8), ARCH_POLYGON) is None
    assert operations.arch_zone_sample(_textured_frame(), ((0.5, 0.5), (0.51, 0.5), (0.51, 0.51))) is None
    sample = operations.arch_zone_sample(_textured_frame(), ARCH_POLYGON)
    assert sample[0].shape == (72, 160) and sample[1].shape == (72, 160)
    with pytest.raises(ValueError):
        operations.arch_zone_flow(None, sample, min_flow_px=1.0)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/infrastructure/vision/test_opencv_frames.py -q -k arch_zone`
Expected: FAIL with `AttributeError: 'OpenCvImageOperations' object has no attribute 'arch_zone_sample'`.

- [ ] **Step 3: Implement the primitives**

In `OpenCvImageOperations` add `from collections.abc import Sequence` to the module imports and, after `cargo_scene_motion`:

```python
    ARCH_SAMPLE_WIDTH = 160

    def arch_zone_sample(
        self,
        frame: Any,
        polygon: Sequence[tuple[float, float]],
    ) -> Any | None:
        """Small normalized grayscale crop of the arch zone plus its polygon mask.

        Brightness is min-max normalized per sample, so day/night and IR
        exposure changes produce no optical flow on their own. The bounding
        box is scaled to ``ARCH_SAMPLE_WIDTH`` so flow thresholds are stable
        across camera resolutions.
        """
        import numpy as np

        if not self.transport_frame_usable(frame):
            return None
        height, width = frame.shape[:2]
        xs = [min(width, max(0, int(round(float(x) * width)))) for x, _y in polygon]
        ys = [min(height, max(0, int(round(float(y) * height)))) for _x, y in polygon]
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        if right - left < 16 or bottom - top < 8:
            return None
        crop = self.cv2.cvtColor(frame[top:bottom, left:right], self.cv2.COLOR_BGR2GRAY)
        scale = self.ARCH_SAMPLE_WIDTH / (right - left)
        sample_height = max(8, int(round((bottom - top) * scale)))
        gray = self.cv2.resize(
            crop,
            (self.ARCH_SAMPLE_WIDTH, sample_height),
            interpolation=self.cv2.INTER_AREA,
        )
        gray = self.cv2.normalize(gray, None, 0, 255, self.cv2.NORM_MINMAX)
        mask = np.zeros(gray.shape, dtype="uint8")
        points = np.array(
            [
                [
                    int(round((float(x) * width - left) * scale)),
                    int(round((float(y) * height - top) * scale)),
                ]
                for x, y in polygon
            ],
            dtype="int32",
        )
        self.cv2.fillPoly(mask, [points], 255)
        return gray, mask

    def arch_zone_flow(
        self,
        previous: Any,
        current: Any,
        *,
        min_flow_px: float,
    ) -> dict[str, float]:
        """Dense optical flow between two zone samples.

        ``moving_fraction`` is the share of zone pixels displaced by at least
        ``min_flow_px``; ``dx``/``dy`` are the mean displacement of those
        pixels (positive ``dx`` = rightwards in the frame).
        """
        import numpy as np

        if previous is None or previous[0].shape != current[0].shape:
            raise ValueError("zone sample shape changed")
        flow = self.cv2.calcOpticalFlowFarneback(
            previous[0], current[0], None, 0.5, 3, 15, 3, 5, 1.2, 0
        )
        inside = current[1] > 0
        if not inside.any():
            raise ValueError("zone mask is empty")
        dx, dy = flow[..., 0][inside], flow[..., 1][inside]
        moving = np.hypot(dx, dy) >= float(min_flow_px)
        return {
            "moving_fraction": float(moving.mean()),
            "dx": float(dx[moving].mean()) if moving.any() else 0.0,
            "dy": float(dy[moving].mean()) if moving.any() else 0.0,
        }
```

- [ ] **Step 4: Run the vision tests**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/infrastructure/vision -q`
Expected: PASS (the `FakeCv2`-based tests are untouched: `FakeCv2` is never asked for the new methods).

- [ ] **Step 5: Commit**

```bash
git add cv_service/src/infrastructure/vision/opencv_frames.py cv_service/tests/infrastructure/vision/test_opencv_frames.py
git commit -m "feat(vision): optical-flow motion measure for a camera zone"
```

---

### Task 4: `ArchMotionMonitor` state machine

**Files:**
- Modify: `cv_service/src/application/automation/settings.py` (append `ArchMonitorSettings`)
- Create: `cv_service/src/application/automation/arch_motion_monitor.py`
- Modify: `cv_service/src/application/automation/__init__.py`
- Test: `cv_service/tests/application/automation/test_arch_motion_monitor.py`

**Interfaces:**
- Consumes: `CaptureLease`, `Clock`, `EventLogger`, `VehicleRoiLoader` Protocols from `.ports`; `normalize_polygon` from `src.domain.vehicle_plate`; sampler/flow callables shaped like Task 3 (`flow` already bound to `min_flow_px` by the caller).
- Produces: `ArchMonitorSettings(inference_fps, moving_fraction, min_flow_px, moving_samples=2, still_samples=3, zone_refresh_seconds=2.0, capture_wait_seconds=0.5, error_log_interval_seconds=60.0, error_backoff_max_seconds=2.0)`; `ArchMotionMonitor(cam, source, *, capture_lease, zone_loader, sampler, flow, settings, clock, logger, error_formatter)` with `start()/stop()/close(timeout)`, `request_zone_refresh()`, `status()` returning

```python
{
  "cam": "cam8", "source": "main", "status": "online",          # starting|online|degraded|stopped|awaiting_comparison|frame_unusable|zone_missing_or_disabled|zone_source_mismatch|zone_error|<capture status>
  "state": "still",                                             # unknown|moving|still
  "still_seconds": 10.5,                                        # 0.0 unless state == "still"
  "direction": "right",                                         # ""|left|right — last confirmed movement
  "moving_fraction": 0.0123,
  "last_frame_at": "2026-09-14T05:00:00.000+00:00",
  "last_sample_at": "...", "sample_age_seconds": 0.3,
  "samples": 41, "consecutive_errors": 0, "last_error": None,
  "zone": {"cam", "configured", "enabled", "source", "points", "updated_at"} | None,
  "capture": {...capture snapshot...},
}
```

- [ ] **Step 1: Write the failing tests**

Create `cv_service/tests/application/automation/test_arch_motion_monitor.py`:

```python
from __future__ import annotations

import threading

import pytest
from src.application.automation import ArchMonitorSettings, ArchMotionMonitor
from src.domain import FramePacket

from tests.application.automation.conftest import (
    EventRecorder,
    FakeCapture,
    FakeClock,
    FakeFrame,
    FakeLease,
)

ZONE = {
    "cam": "cam8",
    "configured": True,
    "enabled": True,
    "source": "main",
    "points": [[0.1, 0.3], [0.9, 0.3], [0.9, 0.7], [0.1, 0.7]],
    "updated_at": "2026-09-14T00:00:00+00:00",
}
CALM = {"moving_fraction": 0.0, "dx": 0.0, "dy": 0.0}
MOVING = {"moving_fraction": 0.9, "dx": 2.4, "dy": 0.0}


class Flow:
    """Scripted optical-flow results, popped in order."""

    def __init__(self, readings):
        self.readings = list(readings)
        self.calls = 0

    def __call__(self, previous, current):
        self.calls += 1
        return self.readings.pop(0)


def settings(**overrides):
    values = {"inference_fps": 4.0, "moving_fraction": 0.15, "min_flow_px": 1.0}
    values.update(overrides)
    return ArchMonitorSettings(**values)


def make_monitor(flow, *, zone_loader=None, clock=None, lease=None, sampler=None, logger=None):
    return ArchMotionMonitor(
        "cam8",
        "main",
        capture_lease=lease or FakeLease(),
        zone_loader=zone_loader or (lambda cam: ZONE),
        sampler=sampler or (lambda frame, polygon: ("sample", len(polygon))),
        flow=flow,
        settings=settings(),
        clock=clock or FakeClock(),
        logger=logger or EventRecorder(),
    )


def run_frames(monitor, count, *, start=100.0, step=0.25):
    now = start
    for _ in range(count):
        monitor._process_frame(FakeFrame(), now=now)
        now += step
    return now


def test_wagon_moving_then_stopping_reports_state_direction_and_still_seconds():
    clock = FakeClock()
    flow = Flow([MOVING, MOVING, CALM, CALM, CALM])
    monitor = make_monitor(flow, clock=clock)
    monitor._refresh_zone(100.0, force=True)

    monitor._process_frame(FakeFrame(), now=100.0)  # seeds the comparison only
    monitor._process_frame(FakeFrame(), now=100.25)  # moving 1 of 2
    assert monitor.status()["state"] == "unknown"
    monitor._process_frame(FakeFrame(), now=100.5)  # moving 2 of 2
    status = monitor.status()
    assert (status["state"], status["direction"], status["still_seconds"]) == ("moving", "right", 0.0)

    monitor._process_frame(FakeFrame(), now=100.75)  # calm 1 of 3
    monitor._process_frame(FakeFrame(), now=101.0)  # calm 2 of 3
    assert monitor.status()["state"] == "moving"
    monitor._process_frame(FakeFrame(), now=101.25)  # calm 3 of 3 → still
    clock.value = 111.25
    status = monitor.status()
    assert status["state"] == "still"
    assert status["still_seconds"] == 10.5  # counted from the first calm sample at 100.75
    assert status["direction"] == "right"
    assert status["samples"] == 5 and flow.calls == 5


def test_single_moving_sample_neither_flips_a_still_zone_nor_resets_its_timer():
    clock = FakeClock()
    flow = Flow([CALM, CALM, CALM, MOVING, CALM, CALM])
    monitor = make_monitor(flow, clock=clock)
    monitor._refresh_zone(100.0, force=True)

    now = run_frames(monitor, 4)  # seed + calm ×3 → still since 100.25
    assert monitor.status()["state"] == "still"
    now = run_frames(monitor, 1, start=now)  # one moving glitch (a person crossing)
    assert monitor.status()["state"] == "still"
    run_frames(monitor, 2, start=now)
    clock.value = 111.25
    status = monitor.status()
    assert status["state"] == "still"
    assert status["still_seconds"] == 11.0
    assert status["direction"] == ""


def test_frame_gap_requires_a_fresh_comparison_before_reporting():
    flow = Flow([CALM, CALM, CALM, CALM])
    monitor = make_monitor(flow)
    monitor._refresh_zone(100.0, force=True)

    run_frames(monitor, 4)
    assert monitor.status()["state"] == "still"
    monitor._process_frame(FakeFrame(), now=110.0)  # 9 s without frames: the pair is not comparable
    status = monitor.status()
    assert (status["state"], status["status"]) == ("unknown", "awaiting_comparison")
    assert flow.calls == 3
    monitor._process_frame(FakeFrame(), now=110.25)
    assert flow.calls == 4


def test_unusable_frame_and_zone_changes_reset_to_unknown():
    zones = {"value": ZONE}
    flow = Flow([CALM] * 6)
    monitor = make_monitor(flow, zone_loader=lambda cam: zones["value"], sampler=lambda frame, polygon: None if frame == "dark" else ("sample", len(polygon)))
    monitor._refresh_zone(100.0, force=True)
    run_frames(monitor, 4)
    assert monitor.status()["state"] == "still"

    monitor._process_frame("dark", now=101.0)
    assert (monitor.status()["state"], monitor.status()["status"]) == ("unknown", "frame_unusable")

    zones["value"] = {**ZONE, "enabled": False, "updated_at": "2026-09-14T00:01:00+00:00"}
    monitor._refresh_zone(200.0, force=True)
    status = monitor.status()
    assert (status["state"], status["status"]) == ("unknown", "zone_missing_or_disabled")
    assert monitor.polygon is None

    zones["value"] = {**ZONE, "source": "sub", "updated_at": "2026-09-14T00:02:00+00:00"}
    monitor._refresh_zone(300.0, force=True)
    assert monitor.status()["status"] == "zone_source_mismatch"


def test_thread_consumes_capture_frames_and_releases_the_lease_on_close():
    packets = [
        FramePacket(sequence=index, captured_at=1_700_000_000.0 + index, captured_monotonic=100.0 + index, frame=FakeFrame())
        for index in range(1, 5)
    ]
    lease = FakeLease(FakeCapture(packets))
    seen = threading.Event()

    class CountingFlow(Flow):
        def __call__(self, previous, current):
            result = super().__call__(previous, current)
            if self.calls >= 2:
                seen.set()
            return result

    monitor = make_monitor(CountingFlow([CALM] * 10), lease=lease)
    monitor.start()
    assert seen.wait(3.0)
    monitor.close(timeout=3.0)
    assert not monitor.is_alive()
    assert lease.close_calls == 1
    assert monitor.status()["status"] == "stopped"


def test_flow_failure_degrades_status_and_logs_once_per_interval():
    class Boom:
        def __call__(self, previous, current):
            raise RuntimeError("cv2 exploded")

    logger = EventRecorder()
    monitor = make_monitor(Boom(), logger=logger)
    monitor._refresh_zone(100.0, force=True)
    monitor._process_frame(FakeFrame(), now=100.0)
    with pytest.raises(RuntimeError):
        monitor._process_frame(FakeFrame(), now=100.25)
    monitor._record_failure(RuntimeError("cv2 exploded"), now=100.25)
    monitor._record_failure(RuntimeError("cv2 exploded"), now=100.5)
    status = monitor.status()
    assert (status["status"], status["consecutive_errors"]) == ("degraded", 2)
    assert [event for event, _fields in logger.events].count("arch_motion_failed") == 1


def test_settings_validate_ranges():
    with pytest.raises(ValueError, match="moving_fraction must be at most 1"):
        settings(moving_fraction=1.5)
    with pytest.raises(ValueError, match="must be positive"):
        settings(still_samples=0)
```

Note on the thread test: the real `FramePacket` dataclass lives in `src.domain.value_objects` and is re-exported from `src.domain` (check `from src.domain import FramePacket` works; `tests/application/automation/test_wagon_monitor.py` already imports it that way — mirror its import). `FakeClock` returns a fixed monotonic value, so the thread test only asserts frame consumption, not timing. Check `EventRecorder` stores `(event, fields)` tuples in `.events` (see its definition in `conftest.py`; adapt the last assertion to its actual attribute name if different).

- [ ] **Step 2: Run them to verify they fail**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/application/automation/test_arch_motion_monitor.py -q`
Expected: FAIL at import: `ImportError: cannot import name 'ArchMonitorSettings'`.

- [ ] **Step 3: Add `ArchMonitorSettings`**

Append to `cv_service/src/application/automation/settings.py`:

```python
@dataclass(frozen=True, slots=True)
class ArchMonitorSettings:
    inference_fps: float
    moving_fraction: float
    min_flow_px: float
    moving_samples: int = 2
    still_samples: int = 3
    zone_refresh_seconds: float = 2.0
    capture_wait_seconds: float = 0.5
    error_log_interval_seconds: float = 60.0
    error_backoff_max_seconds: float = 2.0

    def __post_init__(self) -> None:
        positive_values = {
            "inference_fps": self.inference_fps,
            "moving_fraction": self.moving_fraction,
            "min_flow_px": self.min_flow_px,
            "moving_samples": self.moving_samples,
            "still_samples": self.still_samples,
            "zone_refresh_seconds": self.zone_refresh_seconds,
            "capture_wait_seconds": self.capture_wait_seconds,
            "error_log_interval_seconds": self.error_log_interval_seconds,
            "error_backoff_max_seconds": self.error_backoff_max_seconds,
        }
        invalid = [name for name, value in positive_values.items() if value <= 0]
        if invalid:
            raise ValueError(f"{', '.join(invalid)} must be positive")
        if self.moving_fraction > 1:
            raise ValueError("moving_fraction must be at most 1")
```

- [ ] **Step 4: Implement the monitor**

Create `cv_service/src/application/automation/arch_motion_monitor.py`:

```python
"""Motion state of the wagon-scale unloading arch, measured in one camera zone."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from src.domain.vehicle_plate import normalize_polygon

from .ports import CaptureLease, Clock, EventLogger, VehicleRoiLoader
from .settings import ArchMonitorSettings

ErrorFormatter = Callable[[BaseException], str]
ZoneSampler = Callable[[Any, Sequence[tuple[float, float]]], Any | None]
ZoneFlow = Callable[[Any, Any], Mapping[str, float]]


def _default_error_formatter(error: BaseException) -> str:
    return str(error)[:1000]


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="milliseconds")


class ArchMotionMonitor(threading.Thread):
    """Report whether the arch zone of ``cam`` is ``moving`` or ``still``.

    ``moving_samples`` consecutive moving samples switch the state to
    ``moving``; ``still_samples`` consecutive calm samples switch it to
    ``still``. ``still_seconds`` counts from the first calm sample after the
    last confirmed movement, so a single glitch (a person crossing the zone)
    neither flips the state nor restarts the clock. Optical flow itself is an
    injected callable: this class owns no OpenCV code.
    """

    def __init__(
        self,
        cam: str,
        source: str,
        *,
        capture_lease: CaptureLease,
        zone_loader: VehicleRoiLoader,
        sampler: ZoneSampler,
        flow: ZoneFlow,
        settings: ArchMonitorSettings,
        clock: Clock,
        logger: EventLogger,
        error_formatter: ErrorFormatter = _default_error_formatter,
    ) -> None:
        if source not in {"main", "sub"}:
            raise ValueError("source must be main or sub")
        super().__init__(name=f"arch-motion-{cam}-{source}", daemon=True)
        self.cam = cam
        self.source = source
        self.capture_lease = capture_lease
        self.capture = capture_lease.hub
        self.zone_loader = zone_loader
        self.sampler = sampler
        self.flow = flow
        self.settings = settings
        self.clock = clock
        self.logger = logger
        self.error_formatter = error_formatter
        self.stop_event = threading.Event()
        self.state_lock = threading.Lock()
        self._release_lock = threading.Lock()
        self._capture_released = False
        self.status_text = "starting"
        self.state = "unknown"
        self.direction = ""
        self.moving_fraction = 0.0
        self.moving_streak = 0
        self.still_streak = 0
        self.calm_since: float | None = None
        self.last_frame_at: str | None = None
        self.last_sample_at: float | None = None
        self.samples = 0
        self.previous: Any | None = None
        self.previous_at: float | None = None
        self.zone: dict[str, Any] | None = None
        self.zone_signature: str | None = None
        self.polygon: tuple[tuple[float, float], ...] | None = None
        self.next_zone_refresh = 0.0
        self.consecutive_errors = 0
        self.last_error: str | None = None
        self.last_error_log = float("-inf")

    # -- zone -------------------------------------------------------------

    def _normalized_zone(self, value: Mapping[str, Any]) -> dict[str, Any]:
        points = value.get("points", [])
        if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
            raise TypeError("arch zone points must be an array")
        return {
            "cam": str(value.get("cam", self.cam)),
            "configured": bool(value.get("configured", False)),
            "enabled": bool(value.get("enabled", False)),
            "source": str(value.get("source", self.source)),
            "points": list(points),
            "updated_at": value.get("updated_at"),
        }

    def _refresh_zone(self, now: float, *, force: bool = False) -> None:
        if not force and now < self.next_zone_refresh:
            return
        self.next_zone_refresh = now + self.settings.zone_refresh_seconds
        config = self._normalized_zone(self.zone_loader(self.cam))
        signature = json.dumps(
            {
                "enabled": config["enabled"],
                "source": config["source"],
                "points": config["points"],
                "updated_at": config["updated_at"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        if signature == self.zone_signature:
            return
        first_load = self.zone_signature is None
        self.zone = config
        self.zone_signature = signature
        usable = config["configured"] and config["enabled"] and config["source"] == self.source
        polygon = tuple(normalize_polygon(config["points"])) if usable else None
        with self.state_lock:
            self.polygon = polygon
            self._reset_comparison_locked()
            if polygon is not None:
                self.status_text = "awaiting_comparison"
            elif config["configured"] and config["enabled"]:
                self.status_text = "zone_source_mismatch"
            else:
                self.status_text = "zone_missing_or_disabled"
        self.logger(
            "arch_zone_loaded" if first_load else "arch_zone_reloaded",
            cam=self.cam,
            enabled=config["enabled"],
            configured=config["configured"],
            source=config["source"],
            monitor_source=self.source,
            points=len(config["points"]),
        )

    def request_zone_refresh(self) -> None:
        """Make a newly persisted zone visible on the monitor's next loop."""

        with self.state_lock:
            self.next_zone_refresh = 0.0

    # -- state machine ------------------------------------------------------

    def _reset_comparison_locked(self) -> None:
        self.previous = None
        self.previous_at = None
        self.state = "unknown"
        self.moving_streak = 0
        self.still_streak = 0
        self.calm_since = None

    def _process_frame(self, frame: Any, *, now: float) -> None:
        if self.polygon is None:
            return
        sample = self.sampler(frame, self.polygon)
        if sample is None:
            with self.state_lock:
                self._reset_comparison_locked()
                self.status_text = "frame_unusable"
            return
        previous, previous_at = self.previous, self.previous_at
        self.previous, self.previous_at = sample, now
        max_gap = 3.0 / self.settings.inference_fps
        if previous is None or previous_at is None or now - previous_at > max_gap:
            with self.state_lock:
                self.state = "unknown"
                self.moving_streak = self.still_streak = 0
                self.calm_since = None
                self.status_text = "awaiting_comparison"
            return
        measure = self.flow(previous, sample)
        fraction = float(measure["moving_fraction"])
        with self.state_lock:
            self.samples += 1
            self.moving_fraction = fraction
            self.last_sample_at = now
            self.status_text = "online"
            self.consecutive_errors = 0
            self.last_error = None
            if fraction >= self.settings.moving_fraction:
                self.moving_streak += 1
                self.still_streak = 0
                if self.moving_streak >= self.settings.moving_samples:
                    self.state = "moving"
                    self.calm_since = None
                    dx = float(measure.get("dx", 0.0))
                    if dx:
                        self.direction = "right" if dx > 0 else "left"
            else:
                self.still_streak += 1
                self.moving_streak = 0
                if self.calm_since is None:
                    self.calm_since = now
                if self.still_streak >= self.settings.still_samples:
                    self.state = "still"

    def _record_failure(self, error: BaseException, *, now: float) -> None:
        safe_error = self.error_formatter(error)
        with self.state_lock:
            self.status_text = "degraded"
            self.consecutive_errors += 1
            failures = self.consecutive_errors
            self.last_error = safe_error
        if failures == 1 or now - self.last_error_log >= self.settings.error_log_interval_seconds:
            self.last_error_log = now
            self.logger(
                "arch_motion_failed",
                level="warning",
                cam=self.cam,
                source=self.source,
                consecutive_errors=failures,
                error=safe_error,
            )

    # -- thread -------------------------------------------------------------

    def _release_capture(self) -> None:
        with self._release_lock:
            if self._capture_released:
                return
            self._capture_released = True
        self.capture_lease.close()

    def run(self) -> None:
        try:
            sequence = 0
            period = 1.0 / self.settings.inference_fps
            next_sample = self.clock.monotonic()
            while not self.stop_event.is_set():
                refresh_now = self.clock.monotonic()
                try:
                    self._refresh_zone(refresh_now)
                except Exception as error:  # noqa: BLE001 - adapter boundary
                    with self.state_lock:
                        self.status_text = "zone_error"
                        self.last_error = self.error_formatter(error)
                    self.stop_event.wait(1.0)
                    continue
                try:
                    packet = self.capture.wait_next(
                        sequence, timeout=self.settings.capture_wait_seconds
                    )
                    if packet is None:
                        capture_status = self.capture.snapshot()
                        with self.state_lock:
                            if self.polygon is not None and self.previous is None:
                                self.status_text = str(capture_status.get("status", "waiting"))
                        continue
                    sequence = packet.sequence
                    now = self.clock.monotonic()
                    with self.state_lock:
                        self.last_frame_at = _iso(packet.captured_at)
                    if self.polygon is None or now < next_sample:
                        continue
                    next_sample = now + period
                    self._process_frame(packet.frame, now=now)
                except Exception as error:  # noqa: BLE001 - adapter boundary
                    now = self.clock.monotonic()
                    self._record_failure(error, now=now)
                    self.stop_event.wait(min(self.settings.error_backoff_max_seconds, period))
        finally:
            self._release_capture()
            with self.state_lock:
                self.status_text = "stopped"

    def stop(self) -> None:
        self.stop_event.set()

    def close(self, timeout: float | None = None) -> None:
        """Stop the monitor and release its capture ownership."""

        self.stop()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=timeout)
        if not self.is_alive():
            self._release_capture()

    def status(self) -> dict[str, Any]:
        now = self.clock.monotonic()
        with self.state_lock:
            still_seconds = (
                round(now - self.calm_since, 1)
                if self.state == "still" and self.calm_since is not None
                else 0.0
            )
            return {
                "cam": self.cam,
                "source": self.source,
                "status": self.status_text,
                "state": self.state,
                "still_seconds": still_seconds,
                "direction": self.direction,
                "moving_fraction": round(self.moving_fraction, 4),
                "last_frame_at": self.last_frame_at,
                "last_sample_at": _iso(self.clock.utc_now().timestamp() - (now - self.last_sample_at))
                if self.last_sample_at is not None
                else None,
                "sample_age_seconds": round(now - self.last_sample_at, 1)
                if self.last_sample_at is not None
                else None,
                "samples": self.samples,
                "consecutive_errors": self.consecutive_errors,
                "last_error": self.last_error,
                "zone": dict(self.zone) if self.zone is not None else None,
                "capture": self.capture.snapshot(),
            }

    snapshot = status
```

Note: with the `FakeClock` in tests the thread loop only samples once per `period` in monotonic time, but `FakeClock.monotonic()` is constant — so the thread test asserts `flow.calls >= 2` via `seen`; make sure the loop samples when `now >= next_sample` (true on the first frame since `next_sample = monotonic()`), and on the second frame `now < next_sample` would skip forever with a frozen clock. To keep the thread test honest, compute `next_sample` from the **packet's** `captured_monotonic` instead of the clock: replace `now = self.clock.monotonic()` inside the frame branch by `now = float(packet.captured_monotonic)` and keep the clock only for zone refresh and errors. Do this — it also makes sampling independent of decode latency. Update the docstring accordingly.

In `cv_service/src/application/automation/__init__.py` add the exports:

```python
from .arch_motion_monitor import ArchMotionMonitor
from .settings import ArchMonitorSettings, VehicleMonitorSettings, WagonMonitorSettings
```

and add `"ArchMonitorSettings"`, `"ArchMotionMonitor"` to `__all__`.

- [ ] **Step 5: Run the monitor tests**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/application/automation -q`
Expected: PASS. If `FakeFrame() == "dark"` comparison in the unusable-frame test is awkward, pass the string `"dark"` only through the sampler lambda as written (the monitor never inspects frames).

- [ ] **Step 6: Commit**

```bash
git add cv_service/src/application/automation/settings.py cv_service/src/application/automation/arch_motion_monitor.py cv_service/src/application/automation/__init__.py cv_service/tests/application/automation/test_arch_motion_monitor.py
git commit -m "feat(automation): arch motion monitor with moving/still state machine"
```

---

### Task 5: Composition, `GET /cameras/<cam>/arch-motion`, health and docs

**Files:**
- Modify: `cv_service/src/composition/automation.py` (dataclass fields, `arch_status`, `request_arch_zone_refresh`, builder block after the vehicle block)
- Modify: `cv_service/src/composition/service.py` (`automation_builder(...)` kwargs ~772; `AiHttpApplication(...)` kwargs ~819; single `OpenCvImageOperations(cv2_module)` instance)
- Modify: `cv_service/src/application/http_facade.py` (`arch_motion_status` ctor kwarg; `arch_motion()`; `health()` block)
- Modify: `cv_service/src/application/http_contracts.py`, `cv_service/src/presentation/http/handler.py` (GET `arch-motion`)
- Modify: `cv_service/AI_SERVICE.md` (endpoint table ~line 37; storage table ~532; tree ~973)
- Test: `cv_service/tests/composition/test_arch_motion_resources.py` (new), `cv_service/tests/application/test_http_facade.py`, `cv_service/tests/presentation/http/test_handler.py`

**Interfaces:**
- Produces: `AutomationResources.arch_monitors`, `arch_status() -> {"enabled","configured_cameras","source","fps","moving_fraction","min_flow_px","moving_samples","still_samples","monitors": {cam: status}}`, `request_arch_zone_refresh(camera) -> bool`; `build_automation_resources(..., arch_zone_repository=None, image_ops=None)`; `AiHttpApplication(arch_motion_status=...)`; `GET /cameras/<cam>/arch-motion` → the monitor `status()` dict (200) or 404 `{"error": "arch motion is not configured for this camera", "cam", "enabled", "configured_cameras"}`; `GET /health` gains `"arch_motion": {...arch_status()}`.

- [ ] **Step 1: Write the failing composition test**

Create `cv_service/tests/composition/test_arch_motion_resources.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

from src.composition.automation import AutomationResources, build_automation_resources
from src.config import load_settings

from tests.application.automation.conftest import FakeClock, FakeLease


class FakePool:
    def __init__(self) -> None:
        self.acquired: list[tuple[str, str]] = []
        self.leases: list[FakeLease] = []

    def acquire(self, cam: str, source: str) -> FakeLease:
        self.acquired.append((cam, source))
        lease = FakeLease()
        self.leases.append(lease)
        return lease


class ZoneRepository:
    def get(self, camera: str) -> dict:
        return {"cam": camera, "configured": False, "enabled": False, "source": "main",
                "coordinate_space": "normalized", "points": [], "updated_at": None}


class ImageOps:
    def arch_zone_sample(self, frame, polygon):
        return ("sample", len(polygon))

    def arch_zone_flow(self, previous, current, *, min_flow_px):
        return {"moving_fraction": 0.0, "dx": 0.0, "dy": 0.0}


def _build(tmp_path, env):
    settings = load_settings({"AI_SERVICE_API_KEY_SHA256": "a" * 64, **env}, tmp_path)
    pool = FakePool()
    resources = build_automation_resources(
        settings,
        SimpleNamespace(wagon_worker=None, vehicle_worker=None),
        store=None,
        capture_pool=pool,
        wagon_recognition=SimpleNamespace(ocr_ready=lambda: False),
        vehicle_recognition=SimpleNamespace(ocr_ready=lambda: False),
        vehicle_roi_repository=None,
        delivery=SimpleNamespace(wagon=None, vehicle=None),
        clock=FakeClock(),
        logger=lambda *args, **kwargs: None,
        notifier=lambda *args, **kwargs: None,
        safe_error=str,
        arch_zone_repository=ZoneRepository(),
        image_ops=ImageOps(),
    )
    return resources, pool


def test_arch_monitors_are_built_only_when_enabled_and_report_status(tmp_path):
    resources, pool = _build(tmp_path, {"AI_ARCH_MOTION_ENABLED": "true", "AI_ARCH_MOTION_CAMERAS": "cam8"})
    try:
        assert list(resources.arch_monitors) == ["cam8"]
        assert pool.acquired == [("cam8", "main")]
        status = resources.arch_status()
        assert status["enabled"] is True and status["configured_cameras"] == ["cam8"]
        assert status["monitors"]["cam8"]["state"] == "unknown"
        assert resources.request_arch_zone_refresh("cam8") is True
        assert resources.request_arch_zone_refresh("cam9") is False
    finally:
        resources.close(timeout=1.0)
    assert pool.leases[0].close_calls == 1

    disabled, disabled_pool = _build(tmp_path, {})
    assert disabled.arch_monitors == {} and disabled_pool.acquired == []
    assert disabled.arch_status()["enabled"] is False


def test_arch_status_is_part_of_automation_resources_defaults(tmp_path):
    settings = load_settings({"AI_SERVICE_API_KEY_SHA256": "a" * 64}, tmp_path)
    assert AutomationResources(settings).arch_status()["monitors"] == {}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/composition/test_arch_motion_resources.py -q`
Expected: FAIL with `TypeError: build_automation_resources() got an unexpected keyword argument 'arch_zone_repository'`.

- [ ] **Step 3: Build the monitors in the composition root**

In `cv_service/src/composition/automation.py`:
- extend the import from `src.application.automation` with `ArchMonitorSettings, ArchMotionMonitor`;
- add `arch_monitors: dict[str, ArchMotionMonitor] = field(default_factory=dict)` to `AutomationResources` and include `*self.arch_monitors.values()` in `_all()`;
- add methods:

```python
    def arch_status(self) -> dict[str, Any]:
        value = self.settings.arch
        return {
            "enabled": value.enabled,
            "configured_cameras": list(value.cameras),
            "source": value.source,
            "fps": value.fps,
            "moving_fraction": value.moving_fraction,
            "min_flow_px": value.min_flow_px,
            "moving_samples": value.moving_samples,
            "still_samples": value.still_samples,
            "monitors": {
                camera: monitor.status()
                for camera, monitor in sorted(self.arch_monitors.items())
            },
        }

    def request_arch_zone_refresh(self, camera: str) -> bool:
        monitor = self.arch_monitors.get(camera)
        if monitor is None:
            return False
        monitor.request_zone_refresh()
        return True
```

- add keyword parameters `arch_zone_repository: Any = None, image_ops: Any = None,` to `build_automation_resources` (after `safe_error: Any,`) and, inside the `try:` after the vehicle block:

```python
        arch = settings.arch
        if arch.enabled:
            if not arch.cameras:
                logger(
                    "arch_motion_configuration_missing",
                    level="warning",
                    reason="no cameras configured",
                )
            elif arch_zone_repository is None or image_ops is None:
                logger("arch_motion_dependencies_missing", level="error")
            else:
                flow = partial(image_ops.arch_zone_flow, min_flow_px=arch.min_flow_px)
                for camera in arch.cameras:
                    lease = capture_pool.acquire(camera, arch.source)
                    try:
                        arch_monitor = ArchMotionMonitor(
                            camera,
                            arch.source,
                            capture_lease=lease,
                            zone_loader=arch_zone_repository.get,
                            sampler=image_ops.arch_zone_sample,
                            flow=flow,
                            settings=ArchMonitorSettings(
                                inference_fps=arch.fps,
                                moving_fraction=arch.moving_fraction,
                                min_flow_px=arch.min_flow_px,
                                moving_samples=arch.moving_samples,
                                still_samples=arch.still_samples,
                            ),
                            clock=clock,
                            logger=logger,
                            error_formatter=safe_error,
                        )
                    except BaseException as construction_error:
                        try:
                            lease.close()
                        except BaseException as cleanup_error:
                            construction_error.add_note(
                                "arch capture lease cleanup also failed: "
                                f"{type(cleanup_error).__name__}: {cleanup_error}"
                            )
                        raise
                    result.arch_monitors[camera] = arch_monitor
```

In `cv_service/src/composition/service.py`: create `image_operations = OpenCvImageOperations(cv2_module)` once before the `automation_builder(...)` call and reuse it for the Telegram `frame_quality=image_operations.transport_frame_usable`; pass `arch_zone_repository=arch_zone_repository, image_ops=image_operations,` to `automation_builder(...)`; pass `arch_motion_status=automation.arch_status, arch_zone_refresher=automation.request_arch_zone_refresh,` to `AiHttpApplication(...)`.

- [ ] **Step 4: Run the composition tests**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/composition -q`
Expected: PASS (including `test_default_automation_builder_call_matches_its_signature`, which only asserts that `vehicle_recognition_command` is absent from the call).

- [ ] **Step 5: Write the failing facade/handler tests for `arch-motion` and health**

In `cv_service/tests/application/test_http_facade.py`: add `arch_motion_status=lambda: {"enabled": True, "configured_cameras": ["cam8"], "monitors": {"cam8": {"state": "still", "still_seconds": 12.0}}},` to the `AiHttpApplication(...)` call inside `build_application`, then append:

```python
def test_arch_motion_returns_the_monitor_status_or_404() -> None:
    application, *_rest = build_application()
    ready = application.arch_motion("cam8")
    assert ready.status == 200 and ready.body == {"state": "still", "still_seconds": 12.0}
    missing = application.arch_motion("cam9")
    assert missing.status == 404
    assert missing.body == {
        "error": "arch motion is not configured for this camera",
        "cam": "cam9",
        "enabled": True,
        "configured_cameras": ["cam8"],
    }
    assert application.health().body["arch_motion"]["monitors"]["cam8"]["state"] == "still"
```

In `cv_service/tests/presentation/http/test_handler.py` add `("/cameras/cam1/arch-motion", "arch_motion"),` to the GET parametrize list.

- [ ] **Step 6: Run them to verify they fail**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests/application/test_http_facade.py tests/presentation/http/test_handler.py -q -k "arch_motion or get_routes_delegate"`
Expected: FAIL (`unexpected keyword argument 'arch_motion_status'`; GET returns 404).

- [ ] **Step 7: Implement `arch_motion`, the contract, the handler and health**

Facade `__init__`: add `arch_motion_status: Callable[[], Mapping[str, Any]] = _empty,` (next to `vehicle_automation_status`) and store it. Add the method after `update_arch_zone`:

```python
    def arch_motion(self, camera: str) -> HttpResult:
        status = dict(self.arch_motion_status())
        monitors = status.get("monitors") or {}
        monitor = monitors.get(camera) if isinstance(monitors, Mapping) else None
        if monitor is None:
            return HttpResult(
                404,
                {
                    "error": "arch motion is not configured for this camera",
                    "cam": camera,
                    "enabled": bool(status.get("enabled", False)),
                    "configured_cameras": list(status.get("configured_cameras", [])),
                },
            )
        return HttpResult.ok(dict(monitor))
```

In `health()` add `"arch_motion": dict(self.arch_motion_status()),` after the `"vehicle_number": {...}` block. In `http_contracts.py` add `def arch_motion(self, camera: str) -> HttpResult: ...` after `update_arch_zone`. In `handler.py` `do_GET`, extend the camera-action set to `{"line", "vehicle-roi", "arch-zone", "arch-motion"}` and dispatch `elif route.action == "arch-zone": ...arch_zone(camera)` / `else: ...arch_motion(camera)`.

- [ ] **Step 8: Document the endpoints**

In `cv_service/AI_SERVICE.md`: add two rows after the `vehicle-roi` row of the endpoint table:

```
| `GET/PUT` | `/cameras/cam8/arch-zone` | Получить или сохранить зону арки вагонных весов (тот же формат, что vehicle ROI) |
| `GET` | `/cameras/cam8/arch-motion` | Состояние зоны арки: `state` moving/still/unknown, `still_seconds`, `direction`, `sample_age_seconds` |
```

add `| \`arch-zones.json\` | Полигоны зоны арки |` to the storage table and `arch-zones.json` to the directory tree; add a short section «Зона арки» next to the vehicle ROI section describing `AI_ARCH_MOTION_*` and that the CRM wagon collector polls `arch-motion` once per second.

- [ ] **Step 9: Run the whole suite and lint**

Run: `cd cv_service && ../.venv/bin/python -m pytest tests -q && ../.venv/bin/python -m ruff check --select E4,E7,E9,F src tests`
Expected: all PASS; ruff clean (if ruff is missing locally, note it and rely on CI).

- [ ] **Step 10: Commit**

```bash
git add cv_service/src/composition/automation.py cv_service/src/composition/service.py cv_service/src/application/http_facade.py cv_service/src/application/http_contracts.py cv_service/src/presentation/http/handler.py cv_service/AI_SERVICE.md cv_service/tests/composition/test_arch_motion_resources.py cv_service/tests/application/test_http_facade.py cv_service/tests/presentation/http/test_handler.py
git commit -m "feat: run arch motion monitors and expose GET /cameras/<cam>/arch-motion"
```

---

### Task 6: Windows installer parameter `-ArchMotionCameras`

**Files:**
- Modify: `deploy/camera-pc/install-ai-service.ps1` (param block ~line 22; settings hashtables ~330; existing-settings merge ~490; validation ~786; settings JSON emission ~1626)
- Modify: `deploy/camera-pc/README.md` (parameter list)
- Test: `deploy/camera-pc/tests/Invoke-StaticTests.ps1` runs in CI (`windows-deployment` job); locally there is no PowerShell runner on macOS — verify by reading and by CI.

**Interfaces:**
- Produces: settings JSON keys `arch_motion_enabled` (bool) and `arch_motion_cameras` (csv string) consumed by `service_launcher.py` (Task 1). Enabling on the plant PC: `install-ai-service.ps1 ... -ArchMotionCameras cam8`.

- [ ] **Step 1: Add the parameter**

After `[string]$VehicleOnDemandCameras = '',` add `[string]$ArchMotionCameras = '',`.

- [ ] **Step 2: Add the settings hashtable**

After the `$vehicleSettings = [ordered]@{ ... }` block add:

```powershell
$archSettings = [ordered]@{
    cameras = $ArchMotionCameras
}
```

- [ ] **Step 3: Keep an existing value when the parameter is not passed**

Next to the `VehicleOnDemandCameras` merge block add:

```powershell
        if (-not $PSBoundParameters.ContainsKey('ArchMotionCameras') -and $names -contains 'arch_motion_cameras') {
            $archSettings.cameras = [string]$existingSettings.arch_motion_cameras
        }
```

- [ ] **Step 4: Validate and normalize**

After the `$vehicleSettings.on_demand_cameras = ($vehicleOnDemandCameraValues -join ',')` line add:

```powershell
$archMotionCameraValues = @(([string]$archSettings.cameras).Split(',') | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ })
if (@($archMotionCameraValues | Where-Object { $_ -notmatch '^cam[1-9]\d*$' }).Count -gt 0) {
    throw 'ArchMotionCameras must be a comma-separated cam<N> list'
}
$archSettings.cameras = ($archMotionCameraValues -join ',')
```

- [ ] **Step 5: Emit the settings JSON keys**

Next to `vehicle_on_demand_cameras = [string]$vehicleSettings.on_demand_cameras` add:

```powershell
        arch_motion_enabled = ($archMotionCameraValues.Count -gt 0)
        arch_motion_cameras = [string]$archSettings.cameras
```

- [ ] **Step 6: Document and verify**

Add `-ArchMotionCameras` to the parameter table in `deploy/camera-pc/README.md` («камеры, на которых считается движение в зоне арки вагонных весов; включает `AI_ARCH_MOTION_ENABLED`»). Run `git diff deploy/camera-pc/install-ai-service.ps1` and re-read the five hunks; push and confirm the `windows-deployment` CI job is green.

- [ ] **Step 7: Commit**

```bash
git add deploy/camera-pc/install-ai-service.ps1 deploy/camera-pc/README.md
git commit -m "feat(installer): -ArchMotionCameras enables the arch motion monitor"
```

---

## Rollout

1. Push `main` of `bag-counter-cv-service`; CI (`python` + `windows-deployment`) must be green.
2. On the plant PC (its own Claude Code session runs the installer, see memory `passage-scale-hands-off`): re-run `install-ai-service.ps1` with the current parameters plus `-ArchMotionCameras cam8`.
3. From the CRM backend container: `GET /cameras/cam8/arch-motion` must answer `state: unknown, status: zone_missing_or_disabled`; then `PUT /cameras/cam8/arch-zone` with the polygon drawn on the cam8 frame (Part 4 gives the UI; until then use the API), and confirm `status: online`, `state` flipping when a wagon passes.

## Self-review

- Spec coverage: zone storage (Task 2), motion state with direction and still_seconds (Tasks 3–4), HTTP `arch-zone`/`arch-motion` (Tasks 2, 5), OSD-clock caveat is operational (zone must not contain the camera timestamp — noted in AI_SERVICE.md section), number reading unchanged. ✔
- Type consistency: `ArchSettings` fields (Task 1) ↔ `ArchMonitorSettings(...)` construction (Task 5) ↔ `arch_status()` keys; `arch_zone_flow(previous, current, *, min_flow_px)` (Task 3) ↔ `partial(image_ops.arch_zone_flow, min_flow_px=...)` (Task 5) ↔ `ZoneFlow` two-arg callable (Task 4). ✔
- No placeholders. ✔
