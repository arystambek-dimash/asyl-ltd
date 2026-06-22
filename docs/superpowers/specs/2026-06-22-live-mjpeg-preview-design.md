# Живой MJPEG-превью обработки видео (разметка модели)

**Дата:** 2026-06-22
**Статус:** утверждён к реализации

## Цель

Во время обработки видео на посту отгрузки оператор видит **живой кадр с
разметкой модели** — боксы вокруг мешков и линию подсчёта — плюс текущее число
посчитанных мешков. Сейчас в UI видно только число; этой фичей добавляем
визуальный поток.

## Контекст

«В очереди» (queued) означает, что воркер не запущен / запущен с ключом не той
камеры. Эта фича **не меняет** это поведение — превью видно только когда воркер
уже обрабатывает задачу (`processing`). Воркер с CV-моделью работает вне Docker
(на GPU-машине), бэкенд-часть превью — в Docker.

## Архитектура и поток данных

```
Воркер (GPU): counter.run_annotated(path) — ОДИН проход YOLO.track(stream=True)
  ├─ на каждый кадр: (event_or_None, annotated_frame)  ← пакет рисует боксы+линию
  │     ├─ event != None → +1 в Redis-счётчик (живое число, как сейчас)
  │     └─ throttle ~330мс → cv2.imencode JPEG → POST /api/video-jobs/{id}/frame/
  └─ в конце → complete{bags:N}

Бэкенд:
  ├─ POST /api/video-jobs/{id}/frame/  (X-Camera-Key) → frame_store.put(id, jpeg), TTL 10с
  └─ GET  /api/video-jobs/{id}/stream/ → StreamingHttpResponse
        multipart/x-mixed-replace; читает frame_store.get(id) в цикле, отдаёт кадры,
        завершается когда job.status != "processing"

Фронт (во время processing):
  └─ <img src="/api/video-jobs/{id}/stream/">  + число мешков
```

**Один проход YOLO** — и подсчёт, и отрисовка в одном цикле; без двойного
инференса.

## CV-пакет (`cv_service_handoff/bag_pipeline.py`)

Добавляем **рядом** с `run()` новый метод `run_annotated(source)`:
- Тот же один проход `YOLO.track(stream=True)` с теми же параметрами.
- Та же логика подсчёта через `LineCrossingCounter` (переиспользуется).
- Дополнительно на копии кадра рисует боксы (`boxes.xyxy`, `cv2.rectangle`) и
  линию подсчёта (`cv2.line`); подпись текущего числа.
- `yield (event_or_None, annotated_frame)` на **каждом** кадре: `event` — dict при
  пересечении линии (формат как у `run()`), иначе `None`.

Старый `run()` **не меняется** — сигнатура и поведение прежние (совместимость с
существующим кодом). `bag_counter.py` не трогаем.

## Бэкенд

### `webhooks/frame_store.py` (новый модуль, по образцу `counter_store.py`)
- `put(job_id, jpeg_bytes)` — `SET frame:job:{job_id}` в Redis, `TTL=10с`, бинарно.
- `get(job_id)` — последний JPEG (`bytes`) или `None`.
- `CounterUnavailable` при недоступном Redis (та же семантика, что у counter_store).
- Отдельный redis-клиент с `decode_responses=False` (нужны сырые байты).

### Эндпоинты (`webhooks/video_views.py`)
- `POST /api/video-jobs/<pk>/frame/` — auth `X-Camera-Key` (как `next/`/`complete/`).
  Тело: raw JPEG (`application/octet-stream`) ИЛИ multipart-поле `frame`.
  Кладёт в `frame_store.put(pk, bytes)`. Возвращает 204. 401 при неверном ключе.
- `GET /api/video-jobs/<pk>/stream/` — без auth (читается тегом `<img>`).
  `StreamingHttpResponse(content_type="multipart/x-mixed-replace; boundary=frame")`.
  Генератор: пока `job.status == "processing"` — читает `frame_store.get(pk)`,
  если кадр есть, отдаёт `--frame\r\nContent-Type: image/jpeg\r\n\r\n<bytes>\r\n`,
  спит ~300мс; ограничение по числу итераций (напр. 1200 ≈ 6 минут) как страховка
  от бесконечного цикла; завершается, когда статус != processing.

### URL-маршруты (`webhooks/urls.py`)
Литеральные `video-jobs/<pk>/frame/` и `video-jobs/<pk>/stream/` ставятся **до**
`router.urls` (как остальные video-jobs sub-paths), чтобы detail-маршрут роутера
их не перехватил.

## Воркер (`integrations/video_worker.py`)

- Заменить `for _event in counter.run(path)` на
  `for event, frame in counter.run_annotated(path)`.
- `if event: n += 1; POST /webhook/camera/ {increment:1}` (как сейчас).
- Throttle по времени (~330мс): `ok, buf = cv2.imencode(".jpg", frame, [IMWRITE_JPEG_QUALITY, 70])`
  → `POST /api/video-jobs/{id}/frame/` с телом `buf.tobytes()`,
  `Content-Type: application/octet-stream`, заголовок `X-Camera-Key`.
- В конце `complete{bags:n}` (без изменений).
- Деградация: ошибки `/frame/` ловим и игнорируем — счёт критичен, превью нет.

## Фронт (`shipping/page.tsx`, компонент `VideoCounter`)

- Во время `job.status === "processing"` рендерим
  `<img src="{NEXT_PUBLIC_API_URL}/video-jobs/{job.id}/stream/">` — живой MJPEG.
- Рядом/поверх — текущее число мешков (как сейчас).
- Фолбэк: `onError` у `<img>` → показываем блок с числом без картинки.
- На `queued`/`done`/`failed` — `<img>` не рендерим.

## Тестирование

- `frame_store` (fakeredis): `put/get`, TTL, `None` при пустом, `CounterUnavailable`
  при недоступном Redis.
- `POST /frame/`: 204 при валидном ключе + кадр в store; 401 при неверном ключе.
- `GET /stream/`: `content_type` начинается с `multipart/x-mixed-replace`; для
  задачи не в `processing` генератор сразу завершается (без зависания теста).
- CV `run_annotated` юнит-тестами не покрываем (нужны torch/веса) — проверка на
  GPU вручную; `run()` остаётся как был.
- Воркер — вне Docker, юнит-тестами не покрываем.

## Сборка
Бэкенд-часть (frame_store + эндпоинты + маршруты) — в Docker, `docker compose up
--build`. Воркер и CV-пакет — на GPU-машине по README.

## Вне scope (YAGNI)
- Настоящий видеокодек живьём (WebRTC/HLS/H.264) — избыточно для МВП.
- Сохранение размеченного .mp4 для пересмотра — отдельная фича.
- Изменение `run()` / `bag_counter.py`.
- Исправление «В очереди» (это вопрос запуска воркера, не превью).
