# Video Upload → Bag Counter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator upload an .mp4 for a `loading` order on the shipping post; CRM queues it, a separate CV worker (with GPU, outside the backend Docker) pulls it, runs the existing `BagColorCounter` unchanged, posts `+1` per bag to the existing Redis counter (live count), and writes the total to the order via the existing `record_loading`.

**Architecture:** A new `VideoJob` model + a thin queue API (upload, atomic `next`, `complete`, `fail`, `requeue`) reusing the camera `X-Camera-Key` auth and the existing Redis/`record_loading` paths. The CV worker is a standalone adapter that never modifies CV code. The order card gains an upload button, job-status badge, and the live counter (reusing `/count/{camera}/`).

**Tech Stack:** Django 5, DRF, PostgreSQL, Redis; Next.js 15; the existing `cv_service_handoff` package (torch/ultralytics) lives only in the worker env.

## Global Constraints

- Reuse existing infra: Redis live-count, `/webhook/camera/` increment mode, `record_loading`, `/count/{camera}/`. No parallel counting/writing paths.
- CV code (`bag_pipeline.py`, weights) is used as-is; only wrapped by `integrations/video_worker.py`. CV deps (torch/ultralytics/opencv) live in the worker env, NOT the backend Docker image.
- `VideoJob.status ∈ {queued, processing, done, failed}`. The "counter camera" = first active `Camera` with `kind="counter"`.
- Worker endpoints (`next`/`complete`/`fail`) authenticate by `X-Camera-Key` (no JWT), like the webhook. Upload uses JWT + `shipping.load`; status read uses `shipping.view`.
- `next/` claims a job atomically (`select_for_update`) queued→processing. Empty queue → 204.
- `complete/` is transactional: `record_loading(order, bags)` + status=done + reset Redis. Business error → 400, status unchanged.
- Media in `MEDIA_ROOT`/`MEDIA_URL`; Docker volume `mediadata`. Upload validates extension (`.mp4/.avi/.mov`) and size (≤200 MB).
- Error shape `{"detail","code"}`, Russian messages. Tests first (TDD), backend from `backend/` with venv (fakeredis where Redis is touched).

---

## File Structure

```
backend/
  config/settings.py            # +MEDIA_ROOT/MEDIA_URL, +DATA_UPLOAD_MAX size
  config/urls.py                # serve media in DEBUG
  webhooks/
    models.py                   # +VideoJob
    serializers.py              # VideoJobSerializer
    video_views.py              # upload, next, complete, fail, requeue, list
    urls.py                     # wire video routes
    admin.py                    # register VideoJob
    tests/test_video_jobs.py
docker-compose.yml              # +mediadata volume on backend
frontend/
  src/lib/types.ts              # VideoJob type
  src/app/shipping/page.tsx     # upload button + job status + live counter in card
integrations/
  video_worker.py               # CV adapter (download → run → +1 → complete)
  README.md                     # +video worker usage
```

---

### Task 1: VideoJob model + media settings

**Files:**
- Modify: `backend/webhooks/models.py`, `backend/webhooks/admin.py`, `backend/config/settings.py`, `backend/config/urls.py`, `docker-compose.yml`

**Interfaces:**
- Produces: `webhooks.models.VideoJob(order FK, camera FK, video FileField, status, bags_counted int, error str, created_at, started_at null, finished_at null)`.

- [ ] **Step 1: Add the model**

In `backend/webhooks/models.py` (it already has `from django.conf import settings`) append:
```python
def video_upload_path(instance, filename):
    return f"videos/order_{instance.order_id}/{filename}"


class VideoJob(models.Model):
    STATUSES = [("queued", "В очереди"), ("processing", "Обработка"),
                ("done", "Готово"), ("failed", "Ошибка")]

    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="video_jobs")
    camera = models.ForeignKey(Camera, on_delete=models.SET_NULL, null=True, related_name="video_jobs")
    video = models.FileField(upload_to=video_upload_path)
    status = models.CharField(max_length=12, default="queued")
    bags_counted = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
```

- [ ] **Step 2: Register in admin**

`backend/webhooks/admin.py`:
```python
from django.contrib import admin
from .models import Camera, WebhookCall, CountSession, VideoJob

admin.site.register([Camera, WebhookCall, CountSession, VideoJob])
```

- [ ] **Step 3: Media settings + DEBUG serving**

In `backend/config/settings.py`, after `STATIC_URL` add:
```python
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DATA_UPLOAD_MAX_MEMORY_SIZE = 210 * 1024 * 1024   # 200 МБ видео
FILE_UPLOAD_MAX_MEMORY_SIZE = 210 * 1024 * 1024
```
In `backend/config/urls.py`, at the end of the file add:
```python
from django.conf import settings as _settings
from django.conf.urls.static import static as _static

if _settings.DEBUG:
    urlpatterns += _static(_settings.MEDIA_URL, document_root=_settings.MEDIA_ROOT)
```

- [ ] **Step 4: Migrate**

Run:
```bash
cd backend && . .venv/bin/activate
python manage.py makemigrations webhooks && python manage.py migrate
```
Expected: creates VideoJob.

- [ ] **Step 5: Compose media volume**

In `docker-compose.yml` backend service add a volume mount:
```yaml
    volumes:
      - mediadata:/app/media
```
and add `mediadata:` under top-level `volumes:`.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(video): VideoJob model + media settings/volume"
```

---

### Task 2: Upload endpoint + serializer

**Files:**
- Create: `backend/webhooks/serializers.py` (append VideoJobSerializer), `backend/webhooks/video_views.py`
- Modify: `backend/webhooks/urls.py`
- Test: `backend/webhooks/tests/test_video_jobs.py`

**Interfaces:**
- Consumes: `HasPerm`, `Camera`, `VideoJob`, `orders.models.Order`.
- Produces:
  - `VideoJobSerializer` (fields id, order, status, bags_counted, error, video, created_at).
  - `POST /api/orders/{order_id}/upload-video/` → creates `VideoJob(queued)`; perm `shipping.load`; validates extension; 400 if no counter camera / bad type; returns job.

- [ ] **Step 1: Write the failing tests**

`backend/webhooks/tests/test_video_jobs.py`:
```python
import io
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from webhooks.models import Camera, VideoJob

pytestmark = pytest.mark.django_db


def _counter_cam():
    return Camera.objects.create(name="cnt", camera_id="counter-01", kind="counter",
                                 status="active", api_key="k", is_active=True)


def _loading_order(boss):
    from catalog.models import Grade, Packaging, Product
    from clients.models import Client
    from orders.models import Order, OrderItem, Payment
    from warehouse.services import receive_stock
    from shipments.services import record_arrival, record_loading
    from decimal import Decimal
    g = Grade.objects.create(name="Премиум")
    pk = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    prod = Product.objects.create(grade=g, packaging=pk, price="100.00")
    receive_stock(prod, 100, boss)
    cl = Client.objects.create(first_name="И", last_name="П", phone="x")
    o = Order.objects.create(client=cl, status="paid", truck_number="123ABC02")
    OrderItem.objects.create(order=o, product=prod, quantity=50)
    Payment.objects.create(order=o, amount=o.total_amount)
    record_arrival(o, "123ABC02", Decimal("0"), boss)
    return o


def _mp4():
    return SimpleUploadedFile("test.mp4", b"\x00\x00fake", content_type="video/mp4")


def test_upload_creates_queued_job(auth_client, boss):
    _counter_cam()
    o = _loading_order(boss)
    r = auth_client(boss).post(f"/api/orders/{o.id}/upload-video/",
                               {"video": _mp4()}, format="multipart")
    assert r.status_code == 201
    job = VideoJob.objects.get()
    assert job.status == "queued" and job.order_id == o.id


def test_upload_bad_extension_400(auth_client, boss):
    _counter_cam()
    o = _loading_order(boss)
    bad = SimpleUploadedFile("x.txt", b"hi", content_type="text/plain")
    r = auth_client(boss).post(f"/api/orders/{o.id}/upload-video/",
                               {"video": bad}, format="multipart")
    assert r.status_code == 400


def test_upload_no_permission_403(auth_client, make_user):
    u = make_user(username="plain")
    from clients.models import Client
    from orders.models import Order
    _counter_cam()
    cl = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=cl, status="loading", truck_number="X")
    bad = SimpleUploadedFile("v.mp4", b"x", content_type="video/mp4")
    r = auth_client(u).post(f"/api/orders/{o.id}/upload-video/",
                            {"video": bad}, format="multipart")
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest webhooks/tests/test_video_jobs.py -v`
Expected: FAIL (endpoint missing).

- [ ] **Step 3: Append serializer**

In `backend/webhooks/serializers.py`, add `VideoJob` to the model import line
(`from .models import Camera, WebhookCall, CountSession, VideoJob`) and append:
```python
class VideoJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoJob
        fields = ["id", "order", "status", "bags_counted", "error",
                  "video", "created_at", "finished_at"]
```

- [ ] **Step 4: Implement upload view**

`backend/webhooks/video_views.py`:
```python
import os
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rbac.permissions import HasPerm
from orders.models import Order
from .models import Camera, VideoJob
from .serializers import VideoJobSerializer

ALLOWED_EXT = {".mp4", ".avi", ".mov"}


class UploadVideoView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [HasPerm("shipping.load")]

    def post(self, request, order_id):
        order = Order.objects.filter(pk=order_id).first()
        if order is None:
            return Response({"detail": "Заказ не найден", "code": "order_not_found"}, status=404)
        f = request.FILES.get("video")
        if f is None:
            return Response({"detail": "Файл видео не передан", "code": "no_file"}, status=400)
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in ALLOWED_EXT:
            return Response({"detail": "Недопустимый формат видео", "code": "bad_format"}, status=400)
        camera = Camera.objects.filter(kind="counter", status="active").first()
        if camera is None:
            return Response({"detail": "Нет активной камеры-счётчика", "code": "no_counter"}, status=400)
        job = VideoJob.objects.create(order=order, camera=camera, video=f, status="queued")
        return Response(VideoJobSerializer(job).data, status=201)
```

- [ ] **Step 5: Wire URL**

In `backend/webhooks/urls.py` add the import and path:
```python
from .video_views import UploadVideoView
# inside urlpatterns list (before router.urls is fine):
    path("orders/<int:order_id>/upload-video/", UploadVideoView.as_view()),
```

- [ ] **Step 6: Run to verify pass**

Run: `pytest webhooks/tests/test_video_jobs.py -v`
Expected: 3 PASS.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(video): upload endpoint creates queued VideoJob"
```

---

### Task 3: Worker endpoints — next/complete/fail/requeue

**Files:**
- Modify: `backend/webhooks/video_views.py`, `backend/webhooks/urls.py`
- Test: `backend/webhooks/tests/test_video_jobs.py` (append)

**Interfaces:**
- Consumes: `counter_store`, `record_loading`, `VideoJob`, `Camera`.
- Produces:
  - `GET /api/video-jobs/next/` (header `X-Camera-Key`): atomically claims oldest queued→processing for the camera matching the key; returns `{id, video_url, camera_id}` or 204; 401 bad key.
  - `POST /api/video-jobs/{id}/complete/` (key): `{bags}` → record_loading + done + reset Redis (transaction); 400 on business error.
  - `POST /api/video-jobs/{id}/fail/` (key): `{error}` → failed.
  - `POST /api/video-jobs/{id}/requeue/` (JWT `shipping.load`) → queued.
  - `GET /api/video-jobs/?order={id}` (JWT `shipping.view`) → list.

- [ ] **Step 1: Write the failing tests**

Append to `backend/webhooks/tests/test_video_jobs.py`:
```python
import fakeredis
from rest_framework.test import APIClient
from webhooks import counter_store


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr(counter_store, "_client", fakeredis.FakeRedis())


def _queued_job(boss):
    cam = _counter_cam()
    o = _loading_order(boss)
    return VideoJob.objects.create(order=o, camera=cam, video=_mp4(), status="queued"), cam, o


def test_next_claims_job(boss):
    job, cam, o = _queued_job(boss)
    c = APIClient()
    r = c.get("/api/video-jobs/next/", HTTP_X_CAMERA_KEY="k")
    assert r.status_code == 200
    assert r.data["id"] == job.id and "video_url" in r.data
    job.refresh_from_db()
    assert job.status == "processing"


def test_next_empty_204():
    _counter_cam()
    c = APIClient()
    r = c.get("/api/video-jobs/next/", HTTP_X_CAMERA_KEY="k")
    assert r.status_code == 204


def test_next_bad_key_401():
    _counter_cam()
    c = APIClient()
    r = c.get("/api/video-jobs/next/", HTTP_X_CAMERA_KEY="nope")
    assert r.status_code == 401


def test_complete_records_loading(boss):
    job, cam, o = _queued_job(boss)
    counter_store.increment(cam.pk, by=40)
    c = APIClient()
    c.get("/api/video-jobs/next/", HTTP_X_CAMERA_KEY="k")  # → processing
    r = c.post(f"/api/video-jobs/{job.id}/complete/", {"bags": 40},
               format="json", HTTP_X_CAMERA_KEY="k")
    assert r.status_code == 200
    job.refresh_from_db(); o.refresh_from_db()
    assert job.status == "done" and job.bags_counted == 40
    assert o.status == "loading" and o.shipment.bags_loaded == 40
    assert counter_store.get(cam.pk) == 0


def test_fail_sets_failed(boss):
    job, cam, o = _queued_job(boss)
    c = APIClient()
    r = c.post(f"/api/video-jobs/{job.id}/fail/", {"error": "boom"},
               format="json", HTTP_X_CAMERA_KEY="k")
    assert r.status_code == 200
    job.refresh_from_db()
    assert job.status == "failed" and job.error == "boom"


def test_list_by_order(auth_client, boss):
    job, cam, o = _queued_job(boss)
    r = auth_client(boss).get(f"/api/video-jobs/?order={o.id}")
    assert r.status_code == 200 and len(r.data) == 1
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest webhooks/tests/test_video_jobs.py -v`
Expected: new tests FAIL.

- [ ] **Step 3: Implement worker + list views**

Append to `backend/webhooks/video_views.py`:
```python
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework import viewsets, mixins
from rbac.permissions import PermViewSetMixin
from . import counter_store
from shipments.services import record_loading


def _camera_from_key(request):
    key = request.headers.get("X-Camera-Key", "")
    return Camera.objects.filter(api_key=key).first() if key else None


class VideoNextView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        cam = _camera_from_key(request)
        if cam is None:
            return Response({"detail": "Неверный ключ камеры", "code": "bad_key"}, status=401)
        with transaction.atomic():
            job = (VideoJob.objects.select_for_update(skip_locked=True)
                   .filter(status="queued", camera=cam).order_by("created_at").first())
            if job is None:
                return Response(status=204)
            job.status = "processing"
            job.started_at = timezone.now()
            job.save(update_fields=["status", "started_at"])
        url = request.build_absolute_uri(job.video.url)
        return Response({"id": job.id, "video_url": url, "camera_id": cam.camera_id})


class VideoCompleteView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, pk):
        cam = _camera_from_key(request)
        if cam is None:
            return Response({"detail": "Неверный ключ камеры", "code": "bad_key"}, status=401)
        job = VideoJob.objects.filter(pk=pk, camera=cam).first()
        if job is None:
            return Response({"detail": "Задача не найдена", "code": "not_found"}, status=404)
        bags = int(request.data.get("bags") or 0)
        try:
            with transaction.atomic():
                record_loading(job.order, bags, None)
                job.status = "done"
                job.bags_counted = bags
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "bags_counted", "finished_at"])
        except ValidationError as e:
            d = e.detail
            msg = d.get("detail") if isinstance(d, dict) else str(d)
            return Response({"detail": msg, "code": "invalid"}, status=400)
        try:
            counter_store.reset(cam.pk)
        except counter_store.CounterUnavailable:
            pass
        return Response({"status": "done", "bags": bags, "order_id": job.order_id})


class VideoFailView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, pk):
        cam = _camera_from_key(request)
        if cam is None:
            return Response({"detail": "Неверный ключ камеры", "code": "bad_key"}, status=401)
        job = VideoJob.objects.filter(pk=pk, camera=cam).first()
        if job is None:
            return Response({"detail": "Задача не найдена", "code": "not_found"}, status=404)
        job.status = "failed"
        job.error = str(request.data.get("error", ""))[:500]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at"])
        return Response({"status": "failed"})


class VideoRequeueView(APIView):
    def get_permissions(self):
        return [HasPerm("shipping.load")]

    def post(self, request, pk):
        job = VideoJob.objects.filter(pk=pk).first()
        if job is None:
            return Response({"detail": "Задача не найдена", "code": "not_found"}, status=404)
        job.status = "queued"
        job.started_at = None
        job.error = ""
        job.save(update_fields=["status", "started_at", "error"])
        return Response({"status": "queued"})


class VideoJobViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = VideoJobSerializer
    required_perms = {"list": "shipping.view"}

    def get_queryset(self):
        qs = VideoJob.objects.select_related("order")
        order = self.request.query_params.get("order")
        return qs.filter(order_id=order) if order else qs
```

- [ ] **Step 4: Wire URLs**

In `backend/webhooks/urls.py`:
```python
from .video_views import (UploadVideoView, VideoNextView, VideoCompleteView,
                          VideoFailView, VideoRequeueView, VideoJobViewSet)
# register on router:
router.register("video-jobs", VideoJobViewSet, basename="video-jobs")
# add to urlpatterns (before router.urls):
    path("orders/<int:order_id>/upload-video/", UploadVideoView.as_view()),
    path("video-jobs/next/", VideoNextView.as_view()),
    path("video-jobs/<int:pk>/complete/", VideoCompleteView.as_view()),
    path("video-jobs/<int:pk>/fail/", VideoFailView.as_view()),
    path("video-jobs/<int:pk>/requeue/", VideoRequeueView.as_view()),
```
IMPORTANT: place the literal `video-jobs/next/`, `.../complete/`, `.../fail/`,
`.../requeue/` paths BEFORE `+ router.urls` so the router's `video-jobs/<pk>/`
detail route does not shadow `video-jobs/next/`. (DRF router list/detail are
`video-jobs/` and `video-jobs/<pk>/`; the explicit `next/` etc. must win.)

- [ ] **Step 5: Run to verify pass + full suite**

Run:
```bash
pytest webhooks/tests/test_video_jobs.py -v
pytest -q
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(video): worker queue API (next/complete/fail/requeue) + list"
```

---

### Task 4: CV worker adapter

**Files:**
- Create: `integrations/video_worker.py`
- Modify: `integrations/README.md`

**Interfaces:**
- Consumes: `/api/video-jobs/next/`, `/api/webhook/camera/` (increment), `/api/video-jobs/{id}/complete/` and `/fail/`.
- Produces: standalone worker loop; CV code untouched.

- [ ] **Step 1: Write the worker**

`integrations/video_worker.py`:
```python
"""
Видео-воркер: тянет видео-задачи из CRM, считает мешки готовой моделью
(cv_service_handoff), шлёт +1 в Redis-счётчик (как камера) и итог в complete.
CV-логику НЕ меняем. Зависимости (torch/ultralytics/opencv) — в этом окружении.

    pip install requests   # + torch/ultralytics/opencv из cv_service_handoff
    python integrations/video_worker.py
"""
import os
import sys
import time
import tempfile
import requests

BASE_URL = os.environ.get("ASYL_BASE_URL", "http://localhost:8000")
CAMERA_ID = os.environ.get("ASYL_CAMERA_ID", "counter-01")
CAMERA_KEY = os.environ.get("ASYL_CAMERA_KEY", "ВСТАВЬТЕ_КЛЮЧ")
CV_DIR = os.environ.get("CV_DIR", "/tmp/cv_handoff/cv_service_handoff")
DET = os.environ.get("CV_DET", f"{CV_DIR}/weights/detector.pt")
CLS = os.environ.get("CV_CLS", f"{CV_DIR}/weights/color_classifier.pt")
DEVICE = os.environ.get("CV_DEVICE", "0")  # "0" GPU, "cpu" для теста

H = {"X-Camera-Key": CAMERA_KEY}


def _api(method, path, **kw):
    return requests.request(method, f"{BASE_URL}{path}", timeout=10, **kw)


def process(job):
    # импорт CV здесь, чтобы воркер падал понятно если веса/torch не настроены
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
    try:
        for _event in counter.run(path):          # CV нетронут
            n += 1
            _api("POST", "/api/webhook/camera/",
                 json={"camera_id": CAMERA_ID, "increment": 1}, headers=H)
        _api("POST", f"/api/video-jobs/{job['id']}/complete/",
             json={"bags": n}, headers=H)
        print(f"[worker] job {job['id']} done: {n} мешков")
    except Exception as e:
        _api("POST", f"/api/video-jobs/{job['id']}/fail/",
             json={"error": str(e)[:400]}, headers=H)
        print(f"[worker] job {job['id']} failed: {e}")
    finally:
        os.remove(path)


def main():
    print(f"[worker] polling {BASE_URL} as {CAMERA_ID}")
    while True:
        try:
            r = _api("GET", "/api/video-jobs/next/", headers=H)
            if r.status_code == 204:
                time.sleep(3); continue
            if r.status_code != 200:
                print("[worker] next error:", r.status_code, r.text); time.sleep(5); continue
            process(r.json())
        except KeyboardInterrupt:
            break
        except requests.RequestException as e:
            print("[worker] сеть:", e); time.sleep(5)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Append README**

Append to `integrations/README.md`:
```markdown

## Видео-воркер (загрузка видео в заказ)

Оператор грузит .mp4 в карточке заказа («Пост отгрузки», шаг Загрузка). Видео
встаёт в очередь. Запустите воркер рядом с моделью (GPU):
```bash
export ASYL_BASE_URL=http://localhost:8000
export ASYL_CAMERA_ID=counter-01
export ASYL_CAMERA_KEY=<ключ камеры-счётчика>
export CV_DIR=/path/to/cv_service_handoff
python integrations/video_worker.py
```
Воркер тянет видео, считает мешки готовой моделью, шлёт +1 в Redis (живой счёт
виден в карточке) и итог записывает в заказ. Для теста без GPU: `CV_DEVICE=cpu`.
```

- [ ] **Step 3: Syntax check**

Run: `python3 -m py_compile integrations/video_worker.py && echo OK`
Expected: OK.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat(integrations): video CV worker (download → run → +1 → complete)"
```

---

### Task 5: Frontend — upload + status + live counter in order card

**Files:**
- Modify: `frontend/src/lib/types.ts`, `frontend/src/app/shipping/page.tsx`

**Interfaces:**
- Consumes: `/api/orders/{id}/upload-video/`, `/api/video-jobs/?order=`, `/api/count/{camera}/`.
- Produces: in the expanded shipping card (status loading) an upload button, job-status badge, and a live bag count.

- [ ] **Step 1: Add type**

In `frontend/src/lib/types.ts`:
```typescript
export interface VideoJob {
  id: number; order: number; status: "queued" | "processing" | "done" | "failed";
  bags_counted: number; error: string; video: string;
  created_at: string; finished_at: string | null;
}
```

- [ ] **Step 2: Add a VideoUpload block component inside shipping page**

In `frontend/src/app/shipping/page.tsx`, add imports at top:
```typescript
import { useEffect, useRef } from "react";
import { Upload } from "lucide-react";
import type { VideoJob } from "@/lib/types";
```
(Keep existing imports; `useState` already imported — add `useEffect`, `useRef`.)

Add this component in the file (above `QueueRow`):
```tsx
function VideoCounter({ orderId }: { orderId: number }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [job, setJob] = useState<VideoJob | null>(null);
  const [bags, setBags] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");

  // poll job status
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const { data } = await api.get<VideoJob[]>(`/video-jobs/?order=${orderId}`);
        if (alive && data.length) setJob(data[0]);
      } catch { /* ignore */ }
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(t); };
  }, [orderId]);

  // poll live count while processing
  useEffect(() => {
    if (job?.status !== "processing") return;
    let alive = true;
    const cnt = async () => {
      try {
        const cams = await api.get("/cameras/");
        const counter = (cams.data as { id: number; kind: string; status: string }[])
          .find((c) => c.kind === "counter" && c.status === "active");
        if (!counter) return;
        const { data } = await api.get(`/count/${counter.id}/`);
        if (alive) setBags(data.bags);
      } catch { /* ignore */ }
    };
    cnt();
    const t = setInterval(cnt, 1500);
    return () => { alive = false; clearInterval(t); };
  }, [job?.status]);

  async function upload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true); setError("");
    try {
      const fd = new FormData();
      fd.append("video", file);
      await api.post(`/orders/${orderId}/upload-video/`, fd,
        { headers: { "Content-Type": "multipart/form-data" } });
    } catch (err) { setError(apiError(err)); } finally { setUploading(false); }
    if (fileRef.current) fileRef.current.value = "";
  }

  const statusLabel: Record<string, string> = {
    queued: "В очереди", processing: "Обработка…", done: "Готово", failed: "Ошибка",
  };

  return (
    <div className="flex flex-col gap-2 rounded-lg border bg-[var(--card)] p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">Видео загрузки</span>
        {job && <Badge tone={job.status === "done" ? "success"
          : job.status === "failed" ? "destructive"
          : job.status === "processing" ? "warning" : "muted"}>
          {statusLabel[job.status]}</Badge>}
      </div>

      {job?.status === "processing" && (
        <div className="text-center">
          <div className="text-4xl font-bold tabular-nums">{bags ?? 0}</div>
          <div className="text-xs text-[var(--muted-foreground)]">мешков посчитано</div>
        </div>
      )}
      {job?.status === "done" && (
        <p className="text-sm text-[var(--success)]">Готово: {job.bags_counted} мешков записано.</p>
      )}
      {job?.status === "failed" && (
        <p className="text-sm text-[var(--destructive)]">Ошибка обработки: {job.error || "—"}</p>
      )}

      <input ref={fileRef} type="file" accept="video/mp4,video/avi,video/quicktime"
        className="hidden" onChange={upload} />
      <Button size="sm" variant="outline" disabled={uploading}
        onClick={() => fileRef.current?.click()}>
        <Upload className="size-4" /> {uploading ? "Загрузка…" : "Загрузить видео"}
      </Button>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
    </div>
  );
}
```

- [ ] **Step 3: Render it in the loading-stage action area**

In `frontend/src/app/shipping/page.tsx`, inside the `order.status === "loading"`
block (the action panel), add `<VideoCounter orderId={order.id} />` right after
the existing «Зафиксировать загрузку» / weigh inputs for that stage. Locate the
`{order.status === "loading" && (` action section and add the component within it,
e.g. just before the closing of that block:
```tsx
                  <VideoCounter orderId={order.id} />
```

- [ ] **Step 4: Typecheck + build**

Run:
```bash
cd frontend && npx tsc --noEmit && npm run build
```
Expected: tsc exit 0; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(frontend): video upload + live count + status in shipping card"
```

---

### Task 6: Full-stack verification in Docker

**Files:** none (verification).

- [ ] **Step 1: Rebuild + up**

```bash
cd /Users/dimash/PycharmProjects/asyl-ltd
docker compose build backend frontend && docker compose up -d
sleep 16
```

- [ ] **Step 2: Verify upload + queue flow (no GPU needed for API)**

As admin: create a counter camera; create a paid+arrived order with a truck number;
`POST /api/orders/{id}/upload-video/` (a tiny .mp4) → 201 queued.
As the camera (X-Camera-Key): `GET /api/video-jobs/next/` → 200 with video_url, job
→ processing; `POST /api/video-jobs/{id}/complete/ {bags:7}` → order `loading`,
bags=7, job `done`. `GET /api/video-jobs/?order=` shows done.

- [ ] **Step 3: Shipping page serves**

`curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/shipping` → 200.

- [ ] **Step 4: (optional) real CV on CPU**

In a separate venv with cv_service_handoff installed:
`CV_DEVICE=cpu ASYL_CAMERA_KEY=<key> python integrations/video_worker.py`
upload a real .mp4 → live count rises → done with the bag total.

- [ ] **Step 5: Final commit**

```bash
git add -A && git commit -m "chore: verify video upload counter end-to-end" --allow-empty
```

---

## Self-Review Notes (coverage map)

- §1 VideoJob model + media (FileField, volume) → Task 1.
- §2 upload (shipping.load, ext check, no-counter 400) + worker next/complete/fail (X-Camera-Key) + requeue + list → Tasks 2, 3.
- §3 CV worker adapter (download → run unchanged → +1 → complete) → Task 4.
- §4 UI upload + status badge + live counter in shipping card → Task 5.
- §5 media storage/volume, RBAC, atomic next, transactional complete, reuse record_loading/Redis, tests → Tasks 1,2,3.
- Out of scope (in-UI video playback, auto-retry timer, parallel jobs, CV changes) → not implemented (correct).
