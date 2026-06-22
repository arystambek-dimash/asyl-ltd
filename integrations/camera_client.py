"""
Пример клиента камеры для АСЫЛ-LTD.

Ваша моделька (распознавание номера / счёт мешков) работает с видео Hikvision
и в конце вызывает наш вебхук. Этот файл — готовый шаблон: подставьте свои
значения и вставьте вызов send_to_server() в свою модельку.

Сервер и моделька на одном компьютере → BASE_URL = http://localhost:8000.

Запуск (тест без камеры):
    pip install requests
    python integrations/camera_client.py --plate 777ABC02

С реальным RTSP Hikvision (нужен opencv-python):
    pip install requests opencv-python
    python integrations/camera_client.py --rtsp \
        "rtsp://admin:ПАРОЛЬ@192.168.1.30:554/Streaming/Channels/101"
"""
import argparse
import requests

# ── НАСТРОЙКИ ────────────────────────────────────────────────────────────────
BASE_URL = "http://localhost:8000"          # сервер на этом же ПК
CAMERA_ID = "counter-01"                    # ID камеры из админки
CAMERA_KEY = "ВСТАВЬТЕ_КЛЮЧ_КАМЕРЫ"         # личный ключ камеры (или enroll-ключ)
# ─────────────────────────────────────────────────────────────────────────────


def send_to_server(plate: str, bags: int | None = None,
                   weight_kg: float | None = None) -> dict:
    """Отправить результат на вебхук. Возвращает ответ сервера (что делать)."""
    payload = {"camera_id": CAMERA_ID, "plate": plate}
    if bags is not None:
        payload["bags"] = bags
    if weight_kg is not None:
        payload["weight_kg"] = weight_kg

    resp = requests.post(
        f"{BASE_URL}/api/webhook/camera/",
        json=payload,
        headers={"X-Camera-Key": CAMERA_KEY},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()


# ── Пример: получить видео с Hikvision по RTSP и обработать ───────────────────
def open_hikvision_stream(rtsp_url: str):
    """
    rtsp_url формата:
      rtsp://<логин>:<пароль>@<IP_камеры>:554/Streaming/Channels/101
    (101 = основной поток первого канала; 102 = субпоток)
    """
    import cv2  # импорт здесь, чтобы тест без камеры не требовал opencv
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        raise RuntimeError("Не удалось открыть RTSP-поток — проверьте IP/логин/пароль")
    return cap


def demo_with_rtsp(rtsp_url: str):
    cap = open_hikvision_stream(rtsp_url)
    print("RTSP открыт. Здесь работает ВАША моделька (счёт мешков / номер).")
    # Псевдокод вашей логики:
    #   while loading_in_progress:
    #       ok, frame = cap.read()
    #       plate, bags = your_model(frame)
    # Когда загрузка машины завершена — отправляем итог:
    #   result = send_to_server(plate=plate, bags=bags)
    #   if result.get("allowed"): open_gate()  # ← решение приходит от сервера
    cap.release()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--plate", default="777ABC02", help="госномер (для теста)")
    p.add_argument("--bags", type=int, help="мешков (для камеры-счётчика)")
    p.add_argument("--weight", type=float, help="вес выезда, кг (для камеры выезда)")
    p.add_argument("--rtsp", help="RTSP-URL Hikvision (демо открытия потока)")
    args = p.parse_args()

    if args.rtsp:
        demo_with_rtsp(args.rtsp)
        return

    result = send_to_server(args.plate, bags=args.bags, weight_kg=args.weight)
    print("Ответ сервера:", result)
    if result.get("allowed") or result.get("open") or result.get("open_gate"):
        print("→ Разрешено. Открываем ворота / продолжаем.")
    else:
        print("→ Отказ:", result.get("reason") or result.get("message") or "—")


if __name__ == "__main__":
    main()
