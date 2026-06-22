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
    import cv2
    sys.path.insert(0, CV_DIR)
    from bag_pipeline import BagColorCounter  # CV-логика как есть (run_annotated добавлен рядом)
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
            if now - last_sent >= 0.33:                       # ~3 кадра/сек
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
