# Live MJPEG Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a live annotated frame (bag boxes + counting line) plus the running bag count while a video job is processing, via an MJPEG stream.

**Architecture:** A new `frame_store.py` keeps the latest JPEG per job in Redis (TTL 10s). The worker pushes annotated JPEGs to `POST /frame/`; the backend serves them as `multipart/x-mixed-replace` from `GET /stream/`. The CV package gains a `run_annotated()` generator (next to the untouched `run()`). The frontend renders an `<img>` pointed at the stream during processing.

**Tech Stack:** Django 5 + DRF, redis-py + fakeredis (tests), Next.js 15, OpenCV (worker only), ultralytics YOLO (worker only).

## Global Constraints

- `run()` and `bag_counter.py` in `cv_service_handoff` must NOT change. Only ADD `run_annotated()` to `bag_pipeline.py`.
- Frame store uses Redis with `decode_responses=False` (raw bytes); key `frame:job:{id}`, TTL 10s.
- `/frame/` is `X-Camera-Key`-authenticated (like `next/`/`complete/`); `/stream/` is unauthenticated (read by `<img>`).
- Literal `video-jobs/<pk>/frame/` and `.../stream/` URLs go BEFORE `router.urls`.
- Preview is best-effort: if the frame store / `/frame/` is unavailable, counting must continue.
- Russian UI copy stays Russian. Existing backend tests stay green.
- Worker + CV run outside Docker (GPU machine); backend preview pieces run in Docker.

---

### Task 1: `frame_store.py` — latest JPEG per job in Redis

**Files:**
- Create: `backend/webhooks/frame_store.py`
- Test: `backend/webhooks/tests/test_frame_store.py`

**Interfaces:**
- Produces:
  - `put(job_id: int, jpeg: bytes) -> None` — `SETEX frame:job:{job_id}` TTL 10s.
  - `get(job_id: int) -> bytes | None` — latest JPEG or None.
  - `FrameUnavailable(RuntimeError)` — raised on Redis errors.
  - `_client` module global (monkeypatched in tests, like counter_store).

- [ ] **Step 1: Write the failing test**

Create `backend/webhooks/tests/test_frame_store.py`:

```python
import pytest
import fakeredis
from webhooks import frame_store


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis()  # bytes, decode_responses=False
    monkeypatch.setattr(frame_store, "_client", fake)


def test_put_get_roundtrip():
    assert frame_store.get(5) is None
    frame_store.put(5, b"\xff\xd8jpegbytes")
    assert frame_store.get(5) == b"\xff\xd8jpegbytes"


def test_put_overwrites():
    frame_store.put(5, b"first")
    frame_store.put(5, b"second")
    assert frame_store.get(5) == b"second"


def test_unavailable_raises(monkeypatch):
    class Boom:
        def setex(self, *a, **k): raise __import__("redis").RedisError("down")
        def get(self, *a, **k): raise __import__("redis").RedisError("down")
    monkeypatch.setattr(frame_store, "_client", Boom())
    with pytest.raises(frame_store.FrameUnavailable):
        frame_store.put(1, b"x")
    with pytest.raises(frame_store.FrameUnavailable):
        frame_store.get(1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest webhooks/tests/test_frame_store.py -v`
Expected: FAIL — `ModuleNotFoundError: webhooks.frame_store`.

- [ ] **Step 3: Implement `backend/webhooks/frame_store.py`**

```python
import redis
from django.conf import settings

_client = None
TTL_SECONDS = 10


class FrameUnavailable(RuntimeError):
    pass


def get_client():
    global _client
    if _client is None:
        # decode_responses=False — нам нужны сырые JPEG-байты, не строки.
        _client = redis.from_url(settings.REDIS_URL, decode_responses=False)
    return _client


def _key(job_id: int) -> str:
    return f"frame:job:{job_id}"


def put(job_id: int, jpeg: bytes) -> None:
    try:
        get_client().setex(_key(job_id), TTL_SECONDS, jpeg)
    except redis.RedisError as e:
        raise FrameUnavailable(str(e))


def get(job_id: int):
    try:
        return get_client().get(_key(job_id))
    except redis.RedisError as e:
        raise FrameUnavailable(str(e))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest webhooks/tests/test_frame_store.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/webhooks/frame_store.py backend/webhooks/tests/test_frame_store.py
git commit -m "feat: frame_store keeps latest job JPEG in Redis"
```

---

### Task 2: `POST /frame/` endpoint — worker pushes JPEG

**Files:**
- Modify: `backend/webhooks/video_views.py` (add `VideoFrameView`)
- Modify: `backend/webhooks/urls.py` (route before router)
- Test: `backend/webhooks/tests/test_frame_endpoints.py` (create)

**Interfaces:**
- Consumes: `frame_store.put`, `_camera_from_key` (already in video_views.py).
- Produces: `POST /api/video-jobs/<pk>/frame/` — 204 on success, 401 bad key, 404 job-not-found.

- [ ] **Step 1: Write the failing test**

Create `backend/webhooks/tests/test_frame_endpoints.py`:

```python
import pytest
import fakeredis
from rest_framework.test import APIClient
from webhooks import frame_store
from webhooks.models import Camera, VideoJob

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr(frame_store, "_client", fakeredis.FakeRedis())


def _cam(key="k1"):
    return Camera.objects.create(name="c", camera_id="counter-01", kind="counter",
                                 status="active", api_key=key, is_active=True)


def _job(cam):
    from clients.models import Client
    from orders.models import Order
    cl = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=cl, status="loading", truck_number="X1")
    return VideoJob.objects.create(order=o, camera=cam, status="processing")


def test_frame_post_stores_jpeg():
    cam = _cam(); job = _job(cam)
    r = APIClient().post(f"/api/video-jobs/{job.id}/frame/", b"\xff\xd8frame",
                         content_type="application/octet-stream",
                         HTTP_X_CAMERA_KEY="k1")
    assert r.status_code == 204
    assert frame_store.get(job.id) == b"\xff\xd8frame"


def test_frame_post_bad_key_401():
    cam = _cam(); job = _job(cam)
    r = APIClient().post(f"/api/video-jobs/{job.id}/frame/", b"x",
                         content_type="application/octet-stream",
                         HTTP_X_CAMERA_KEY="wrong")
    assert r.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest webhooks/tests/test_frame_endpoints.py -v`
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Add `VideoFrameView` to `backend/webhooks/video_views.py`**

Add `from . import frame_store` near the other `from . import counter_store` import. Then add this view (place it after `VideoCompleteView`):

```python
class VideoFrameView(APIView):
    authentication_classes = []
    permission_classes = []
    parser_classes = []  # raw body; we read request.body directly

    def post(self, request, pk):
        cam = _camera_from_key(request)
        if cam is None:
            return Response({"detail": "Неверный ключ камеры", "code": "bad_key"}, status=401)
        job = VideoJob.objects.filter(pk=pk, camera=cam).first()
        if job is None:
            return Response({"detail": "Задача не найдена", "code": "not_found"}, status=404)
        data = request.body
        if not data:
            f = request.FILES.get("frame") if hasattr(request, "FILES") else None
            data = f.read() if f else b""
        if data:
            try:
                frame_store.put(job.pk, data)
            except frame_store.FrameUnavailable:
                pass
        return Response(status=204)
```

- [ ] **Step 4: Route it in `backend/webhooks/urls.py`**

Import ONLY `VideoFrameView` now (Task 3 adds `VideoStreamView` to both import and routes). Extend the existing `from .video_views import (...)` line to include `VideoFrameView`:

```python
from .video_views import (UploadVideoView, VideoNextView, VideoCompleteView,
                          VideoFailView, VideoRequeueView, VideoJobViewSet,
                          VideoFrameView)
```

In `urlpatterns`, after the `complete/` line:

```python
    path("video-jobs/<int:pk>/frame/", VideoFrameView.as_view()),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest webhooks/tests/test_frame_endpoints.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/webhooks/video_views.py backend/webhooks/urls.py backend/webhooks/tests/test_frame_endpoints.py
git commit -m "feat: POST /frame/ stores worker JPEG in frame_store"
```

---

### Task 3: `GET /stream/` endpoint — MJPEG multipart

**Files:**
- Modify: `backend/webhooks/video_views.py` (add `VideoStreamView`)
- Modify: `backend/webhooks/urls.py` (route `stream/`)
- Test: `backend/webhooks/tests/test_frame_endpoints.py` (extend)

**Interfaces:**
- Consumes: `frame_store.get`, `VideoJob`.
- Produces: `GET /api/video-jobs/<pk>/stream/` → `StreamingHttpResponse`, `content_type="multipart/x-mixed-replace; boundary=frame"`. Generator stops when `job.status != "processing"`.

- [ ] **Step 1: Write the failing test (append to test_frame_endpoints.py)**

```python
def test_stream_content_type_and_terminates_when_done():
    cam = _cam(key="k2")
    from clients.models import Client
    from orders.models import Order
    cl = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=cl, status="loaded", truck_number="X2")
    # job NOT processing -> generator must terminate immediately (no hang)
    job = VideoJob.objects.create(order=o, camera=cam, status="done")
    r = APIClient().get(f"/api/video-jobs/{job.id}/stream/")
    assert r.status_code == 200
    assert r["Content-Type"].startswith("multipart/x-mixed-replace")
    body = b"".join(r.streaming_content)  # must not block; job is done
    assert body == b"" or b"--frame" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest webhooks/tests/test_frame_endpoints.py::test_stream_content_type_and_terminates_when_done -v`
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Add `VideoStreamView` to `backend/webhooks/video_views.py`**

Add `import time` at the top if not present. Add `from django.http import StreamingHttpResponse`. Then:

```python
class VideoStreamView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, pk):
        boundary = b"--frame"

        def gen():
            # Стрим живёт, пока задача в обработке. Жёсткий потолок итераций —
            # страховка от бесконечного цикла (1200 * 0.3с ≈ 6 минут).
            for _ in range(1200):
                job = VideoJob.objects.filter(pk=pk).only("status").first()
                if job is None or job.status != "processing":
                    break
                try:
                    frame = frame_store.get(pk)
                except frame_store.FrameUnavailable:
                    frame = None
                if frame:
                    yield (boundary + b"\r\n"
                           + b"Content-Type: image/jpeg\r\n"
                           + b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                           + frame + b"\r\n")
                time.sleep(0.3)

        resp = StreamingHttpResponse(
            gen(), content_type="multipart/x-mixed-replace; boundary=frame")
        resp["Cache-Control"] = "no-cache"
        return resp
```

- [ ] **Step 4: Route `stream/` in `backend/webhooks/urls.py`**

Extend the video_views import to include `VideoStreamView`, and add after the `frame/` route:

```python
    path("video-jobs/<int:pk>/stream/", VideoStreamView.as_view()),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest webhooks/tests/test_frame_endpoints.py -v`
Expected: PASS (3 tests). The done-job generator terminates immediately (loop breaks on non-processing status), so `streaming_content` does not block.

- [ ] **Step 6: Commit**

```bash
git add backend/webhooks/video_views.py backend/webhooks/urls.py backend/webhooks/tests/test_frame_endpoints.py
git commit -m "feat: GET /stream/ serves MJPEG from frame_store"
```

---

### Task 4: CV package — `run_annotated()` next to `run()`

**Files:**
- Modify: `/Users/dimash/Downloads/cv_service_handoff/bag_pipeline.py` (ADD method only)

**Interfaces:**
- Produces: `BagColorCounter.run_annotated(source) -> Iterator[tuple[dict | None, "np.ndarray"]]` — yields `(event_or_None, annotated_bgr_frame)` per frame.
- Consumes: existing `self._load`, `self._line_px`, `self._classify`, `LineCrossingCounter`, `anchor_point` (all already in the package).

This task modifies the CV package, which lives OUTSIDE the repo (in Downloads). It is not covered by repo tests; verification is manual on the GPU machine. `run()` is left untouched.

- [ ] **Step 1: Add `run_annotated` method to `BagColorCounter` (after `run`)**

```python
    def run_annotated(self, source):
        """Как run(), но дополнительно отдаёт размеченный кадр на КАЖДОМ кадре.

        yield (event_or_None, frame_bgr):
          event — dict (формат как у run()) в момент пересечения линии, иначе None.
          frame — копия кадра с нарисованными боксами и линией подсчёта.
        CV-логика (детектор, трекер, классификатор, пересечение) — как в run().
        """
        import cv2
        self._load()
        counter = None
        track_color = {}
        fps = 25.0
        frames = 0
        total = 0

        stream = self._det.track(
            source=source, conf=self.conf, iou=self.iou, imgsz=self.imgsz,
            device=self.device, tracker=self.tracker, persist=True, stream=True,
            vid_stride=self.vid_stride, verbose=False,
        )
        for r in stream:
            frames += 1
            img = r.orig_img
            h, w = img.shape[:2]
            lx1, ly1, lx2, ly2 = self._line_px(w, h)
            if counter is None:
                counter = LineCrossingCounter((lx1, ly1, lx2, ly2), direction=self.direction)

            frame = img.copy()
            cv2.line(frame, (int(lx1), int(ly1)), (int(lx2), int(ly2)), (0, 255, 255), 2)

            event_out = None
            boxes = r.boxes
            if boxes is not None and boxes.id is not None:
                ids = [int(x) for x in _tolist(boxes.id)]
                xyxys = _tolist(boxes.xyxy)
                for tid, xyxy in zip(ids, xyxys):
                    x1, y1, x2, y2 = [int(v) for v in xyxy]
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 200, 0), 2)
                    point = anchor_point(xyxy, self.anchor)
                    event = counter.update(
                        track_id=tid, point=point, frame=frames,
                        time_sec=(frames - 1) / fps, class_id=-1,
                        class_name="bag", confidence=1.0, weight_kg=0.0,
                    )
                    if event is None:
                        continue
                    total += 1
                    if tid not in track_color:
                        track_color[tid] = self._classify(_crop(img, xyxy, self.margin))
                    color, cconf = track_color[tid]
                    event_out = {
                        "camera_id": self.camera_id, "track_id": tid, "color": color,
                        "weight_kg": class_weight(color, self.weights),
                        "direction": event.direction, "confidence": round(cconf, 4),
                        "frame": frames, "video_time_sec": round((frames - 1) / fps, 3),
                        "ts_epoch": time.time(),
                        "point": [round(point[0], 1), round(point[1], 1)],
                    }

            cv2.putText(frame, f"Bags: {total}", (12, 36),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
            yield (event_out, frame)
```

- [ ] **Step 2: Syntax-check (no GPU needed)**

Run: `python -c "import ast; ast.parse(open('/Users/dimash/Downloads/cv_service_handoff/bag_pipeline.py').read()); print('ok')"`
Expected: `ok`. (Full run requires GPU + weights — verified manually in Task 7.)

- [ ] **Step 3: Confirm `run()` untouched**

Run: `grep -n "def run(" /Users/dimash/Downloads/cv_service_handoff/bag_pipeline.py`
Expected: original `def run(self, source)` still present, unchanged.

- [ ] **Step 4: Commit (the CV package is outside the repo; copy it into integrations for versioning is OUT OF SCOPE — just note the change in the worker README in Task 6). No repo commit for this file.**

(There is nothing to commit in the repo for this step; the change lives on the GPU machine's copy. Proceed.)

---

### Task 5: Worker — use `run_annotated`, push frames

**Files:**
- Modify: `integrations/video_worker.py`

**Interfaces:**
- Consumes: `BagColorCounter.run_annotated`, `POST /api/video-jobs/{id}/frame/`.

- [ ] **Step 1: Rewrite the `process` function in `integrations/video_worker.py`**

Replace the body of `process(job)` so it uses `run_annotated`, sends `+1` per event, and throttles frame POSTs to ~330ms:

```python
def process(job):
    import cv2
    sys.path.insert(0, CV_DIR)
    from bag_pipeline import BagColorCounter  # CV-логика как есть
    r = requests.get(job["video_url"], timeout=60)
    fd, path = tempfile.mkstemp(suffix=".mp4")
    with os.fdopen(fd, "wb") as f:
        f.write(r.content)
    counter = BagColorCounter(det_weights=DET, cls_weights=CLS, camera_id=CAMERA_ID,
                              line=(0.0, 0.55, 1.0, 0.55), direction="positive",
                              device=DEVICE)
    n = 0
    last_sent = 0.0
    try:
        for event, frame in counter.run_annotated(path):     # CV нетронут (новый метод)
            if event:
                n += 1
                _api("POST", "/api/webhook/camera/",
                     json={"camera_id": CAMERA_ID, "increment": 1}, headers=H)
            now = time.time()
            if now - last_sent >= 0.33:
                last_sent = now
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    try:
                        _api("POST", f"/api/video-jobs/{job['id']}/frame/",
                             data=buf.tobytes(),
                             headers={**H, "Content-Type": "application/octet-stream"})
                    except requests.RequestException:
                        pass  # превью необязательно — счёт важнее
        _api("POST", f"/api/video-jobs/{job['id']}/complete/",
             json={"bags": n}, headers=H)
        print(f"[worker] job {job['id']} done: {n} мешков")
    except Exception as e:
        _api("POST", f"/api/video-jobs/{job['id']}/fail/",
             json={"error": str(e)[:400]}, headers=H)
        print(f"[worker] job {job['id']} failed: {e}")
    finally:
        os.remove(path)
```

- [ ] **Step 2: Syntax-check the worker**

Run: `python -c "import ast; ast.parse(open('integrations/video_worker.py').read()); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add integrations/video_worker.py
git commit -m "feat: worker streams annotated frames via run_annotated"
```

---

### Task 6: Frontend — live MJPEG `<img>` during processing

**Files:**
- Modify: `frontend/src/app/shipping/page.tsx` (the `VideoCounter` component)
- Modify: `integrations/README.md` (note the new `/frame/` + `/stream/` flow)

**Interfaces:**
- Consumes: `GET /api/video-jobs/{id}/stream/`.

- [ ] **Step 1: Read the current `VideoCounter` component**

Open `frontend/src/app/shipping/page.tsx`, lines ~23-110 (the `VideoCounter` function). Locate the `job?.status === "processing"` block that shows `{bags ?? 0}` + "мешков посчитано".

- [ ] **Step 2: Add the live image inside the processing block**

Determine the API base URL the app uses (the file already imports `api` from `@/lib/api`; the env var is `NEXT_PUBLIC_API_URL`). Add an `<img>` above the number when processing. Replace the processing block:

```tsx
      {job?.status === "processing" && (
        <div className="flex flex-col items-center gap-2">
          <img
            src={`${process.env.NEXT_PUBLIC_API_URL}/video-jobs/${job.id}/stream/`}
            alt="Обработка видео"
            className="w-full max-w-md rounded-lg border bg-black/5"
            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
          />
          <div className="text-center">
            <div className="text-4xl font-bold tabular-nums">{bags ?? 0}</div>
            <div className="text-xs text-[var(--muted-foreground)]">мешков посчитано</div>
          </div>
        </div>
      )}
```

(`onError` hides the image if the stream is unavailable; the number still shows — best-effort preview.)

- [ ] **Step 3: Build the frontend**

Run: `cd frontend && npm run build`
Expected: build succeeds, no type errors.

- [ ] **Step 4: Update `integrations/README.md`**

Add a short note under the video-worker section: "Воркер также шлёт размеченные кадры (боксы + линия) на `POST /api/video-jobs/{id}/frame/`; во время обработки оператор видит живой поток в карточке (`GET .../stream/`, MJPEG). Превью необязательно — при недоступности счёт продолжается."

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/shipping/page.tsx integrations/README.md
git commit -m "feat: live MJPEG preview in shipping card during processing"
```

---

### Task 7: Full verification

**Files:** none (verification only).

- [ ] **Step 1: Backend suite**

Run: `cd backend && pytest -q`
Expected: all pass (111 + 6 new = ~117).

- [ ] **Step 2: Docker build + stream endpoint smoke**

Run: `docker compose up --build -d` then
`curl -s -o /dev/null -w "%{http_code} %{content_type}\n" "http://localhost:8000/api/video-jobs/1/stream/"`
Expected: services start; the stream route responds (200 with `multipart/x-mixed-replace` if job 1 exists, or it terminates fast for a non-processing/missing job — should NOT hang). Then `docker compose down`.

- [ ] **Step 3: Manual GPU note**

Record in the PR/commit message that `run_annotated` end-to-end (annotated frames visible in the card) is verified manually on the GPU machine, since CPU/torch are not in the backend image.

---

## Notes for the implementer

- `frame_store` mirrors `counter_store` exactly except `decode_responses=False` and `setex` (TTL). Keep them as separate modules — different data (int vs bytes), different lifetime.
- The stream generator MUST break on non-processing status — otherwise tests hang and real connections leak. The 1200-iteration cap is the backstop.
- Do not add DRF parsers to `VideoFrameView`; read `request.body` directly (raw bytes). Setting `parser_classes = []` avoids DRF trying to parse octet-stream.
- CV package change is ADD-ONLY: `run()` and `bag_counter.py` stay byte-for-byte identical.
