"""Вебхук номеров с ПК камер без сети: тело события и подписанный POST."""

WEBHOOK_URL = "/api/integrations/vehicle-plate-events"
WEBHOOK_TOKEN = "vehicle-plate-test-token-that-is-long-enough"
EVENT_ID = "0fa68fe2-6fd8-4cc5-93f7-4b90ae690f19"


def payload(*, event_id=EVENT_ID, vehicle_number="123ABC02", **overrides):
    body = {
        "schema_version": 1,
        "event_id": event_id,
        "event_type": "vehicle_plate_detected",
        "detected_at": "2026-08-25T12:30:00.000Z",
        "vehicle_number": vehicle_number,
        "camera": "cam1",
        "source": "main",
        "stationary_seconds": 3.4,
        "confirmation": {
            "votes": 3,
            "detector_confidence": 0.91,
            "ocr_confidence": 0.96,
        },
        "bbox": {
            "pixels": [820, 510, 1050, 590],
            "normalized": {
                "x": 0.320312,
                "y": 0.354167,
                "w": 0.089844,
                "h": 0.055556,
            },
        },
        "vehicle_roi": {
            "coordinate_space": "normalized",
            "points": [
                {"x": 0.38, "y": 0.20},
                {"x": 0.63, "y": 0.32},
                {"x": 0.98, "y": 1.00},
                {"x": 0.18, "y": 1.00},
            ],
        },
        "image": {"width": 2560, "height": 1440},
        "models": {
            "detector": "vehicle-license-plate.pt",
            "ocr": "en_PP-OCRv5_mobile_rec",
        },
    }
    body.update(overrides)
    return body


def post_event(client, body=None, *, token=WEBHOOK_TOKEN, key=None, secure=True):
    body = payload() if body is None else body
    event_id = body.get("event_id", EVENT_ID) if isinstance(body, dict) else EVENT_ID
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {token}",
        "HTTP_IDEMPOTENCY_KEY": key if key is not None else event_id,
    }
    return client.post(
        WEBHOOK_URL,
        body,
        format="json",
        secure=secure,
        **headers,
    )
