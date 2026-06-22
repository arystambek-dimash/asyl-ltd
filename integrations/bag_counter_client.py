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
    #                           camera_id=CAMERA_ID, line=(0.0, 0.55, 1.0, 0.55),
    #                           direction="positive", device="0")
    # for event in counter.run("rtsp://admin:ПАРОЛЬ@192.168.1.64:554/Streaming/Channels/101"):
    #     push_one()
    raise SystemExit("Раскомментируйте run_with_model() и подставьте RTSP/веса.")


if __name__ == "__main__":
    for _ in range(5):
        push_one()
    print("Отправлено 5 инкрементов. Откройте экран «Счётчик мешков».")
