# Загрузка видео → счёт мешков (CV-воркер) — Дизайн

**Дата:** 2026-06-22
**Статус:** согласован, готов к написанию плана реализации

## Цель

Дать оператору на «Посту отгрузки» загрузить тестовое/реальное .mp4 для машины
в статусе `loading`. CRM ставит видео в очередь; отдельный CV-воркер (с GPU,
вне Docker-бэкенда) забирает его, прогоняет через **готовую** CV-модель
(`integrations/cv_service_handoff`, `BagColorCounter`) и на каждый посчитанный
мешок шлёт `+1` в существующий Redis-счётчик (через тот же вебхук, что и реальная
камера). Оператор видит **живой счёт в реальном времени** в карточке заказа; в
конце итог записывается в заказ через существующий `record_loading`.

**Принципы:**
- Переиспользуем готовое: Redis live-count, вебхук increment, `record_loading`.
  Новых путей подсчёта/записи не создаём.
- **CV-логику не трогаем** — `bag_pipeline.py`/веса используются как есть, только
  оборачиваются тонким адаптером (`integrations/video_worker.py`).
- Зависимости CV (`torch`/`ultralytics`/`opencv`) живут в окружении воркера, НЕ в
  бэкенд-Docker.

## 1. Модель данных и поток

Новая модель **`VideoJob`** (приложение `webhooks`):
`order` FK→Order, `camera` FK→Camera (камера-счётчик — владелец Redis-ключа),
`video` (FileField), `status` (`queued`/`processing`/`done`/`failed`),
`bags_counted` (int, итог), `error` (текст), `created_at`, `started_at` (null),
`finished_at` (null).

**Поток:**
```
Оператор (Пост отгрузки, заказ loading) → «Загрузить видео» (.mp4)
  → POST создаёт VideoJob(queued) + сохраняет файл
CV-воркер (отдельный процесс, polling):
  → GET /api/video-jobs/next/ → атомарно queued→processing → {id, video_url, camera_id}
  → download(video_url)
  → BagColorCounter.run(файл): на каждый мешок → POST /webhook/camera/ {increment:1}
       (Redis-счётчик растёт; вебхук НЕ трогаем — тот же путь, что камера)
  → POST /api/video-jobs/{id}/complete/ {bags: N} → done + record_loading + reset Redis
Карточка заказа: поллинг /count/{camera}/ (живой счёт) + /video-jobs/?order= (статус)
```

«Камера-счётчик» для MVP — первая активная камера `kind=counter` (владелец
Redis-ключа и ключа аутентификации воркера).

## 2. API очереди видео

- `POST /api/orders/{id}/upload-video/` — multipart `.mp4`. Создаёт
  `VideoJob(order, camera=counter, status=queued)`. Право `shipping.load`.
- `GET /api/video-jobs/next/` — **для воркера**, аутентификация по `X-Camera-Key`
  (как вебхук, не JWT). Атомарно (`select_for_update`) берёт старейшую `queued` →
  `processing`, отдаёт `{id, video_url, camera_id}`. Пустая очередь → `204`.
- `POST /api/video-jobs/{id}/complete/` — **воркер**, тот же ключ. Тело `{bags:N}`.
  В транзакции: `record_loading(order, N)` (как `close` счётчика) → `status=done`,
  `bags_counted=N`, `finished_at`, сброс Redis-ключа камеры. Ошибка бизнес-логики
  → `400`, статус не меняется.
- `POST /api/video-jobs/{id}/fail/` — **воркер**. Тело `{error}`. `status=failed`.
- `GET /api/video-jobs/?order={id}` — статус для карточки (поллинг). Право
  `shipping.view`.

Живой счёт во время обработки — через существующий `/webhook/camera/`
(increment-режим) и `/count/{camera}/`. Финальная запись — через `record_loading`
(переиспользование, без дублирования).

## 3. CV-воркер (адаптер)

`integrations/video_worker.py` — тонкий адаптер поверх готового CV, вне Docker.
CV-код не меняется.

```python
while True:
    job = GET /api/video-jobs/next/        # X-Camera-Key
    if not job: sleep(3); continue
    path = download(job["video_url"])
    counter = BagColorCounter(det_weights=..., cls_weights=..., camera_id=CAMERA_ID,
                              line=..., direction="positive", device="0")  # как есть
    n = 0
    try:
        for event in counter.run(path):                 # CV-логика нетронута
            n += 1
            POST /webhook/camera/ {camera_id, increment: 1}   # +1 в Redis
        POST /api/video-jobs/{id}/complete/ {bags: n}
    except Exception as e:
        POST /api/video-jobs/{id}/fail/ {error: str(e)}
```

Конфиг (env/вверху файла): `BASE_URL`, `CAMERA_ID`, `CAMERA_KEY`, пути к весам,
`line`/`direction`. Зависимости `torch`/`ultralytics`/`opencv` — в окружении
воркера. Тот же воркер для реальной камеры берёт RTSP вместо файла.

## 4. UI: загрузка видео в карточке заказа

В раскрытой карточке очереди «Пост отгрузки» (шаг Загрузка, статус `loading`):
- **«Загрузить видео»** (`accept="video/mp4"`), право `shipping.load`. Прогресс
  загрузки файла (multipart через axios).
- **Статус задачи**: `В очереди` → `Обработка…` → `Готово: N мешков` / `Ошибка`.
  Поллинг `/video-jobs/?order={id}` ~2 c.
- **Живой счётчик** мешков во время обработки (крупное число, поллинг
  `/count/{camera}/`).
- При `done` мешки уже записаны в заказ (через complete→record_loading); карточка
  обновляется, виден `bags_loaded`.

Встраивается в существующую карточку, не отдельный экран. Готовые компоненты
(Card, Button, Badge).

## 5. Хранение, права, ошибки, тестирование

### Хранение
- Видео в `MEDIA_ROOT` (Django `FileField`), отдаётся по `MEDIA_URL`. Docker:
  volume `mediadata`. Лимит размера (200 МБ) и проверка расширения
  (`.mp4`/`.avi`/`.mov`) при загрузке. Видео не удаляем (MVP).

### Права (RBAC)
- Загрузка — `shipping.load`; статус — `shipping.view`. Воркерские эндпоинты
  (`next`/`complete`/`fail`) — по `X-Camera-Key`, без JWT.

### Целостность
- `next/` — атомарный захват задачи (`select_for_update`), два воркера не возьмут
  одну.
- `complete/` — транзакция (record_loading + done + reset Redis); ошибка → 400,
  статус не меняется.
- Зависшая `processing` (worker умер): ручной retry-эндпоинт
  `POST /api/video-jobs/{id}/requeue/` (право `shipping.load`) — возвращает в
  `queued`.

### Ошибки
- CV-ошибка → `fail/` пишет `error`, карточка показывает «Ошибка обработки».
- Redis недоступен → инкременты падают мягко (вебхук 503), `complete` всё равно
  запишет итог. Единый формат `{"detail","code"}`, русский текст.

### Тестирование (TDD)
- Загрузка → `VideoJob(queued)` с файлом; неверный тип → 400; без права → 403.
- `next/` queued→processing атомарно; пустая очередь → 204; неверный ключ → 401.
- `complete/` → `record_loading` (+мешки), `done`, reset Redis; транзакция.
- `fail/` → `failed` + error. `requeue/` → `queued`.
- `GET /video-jobs/?order=` отдаёт статус.

## Вне рамок (YAGNI)

- Просмотр самого видео в UI (только статус/счёт).
- Авто-ретрай зависших задач по таймеру (только ручной requeue).
- Параллельная обработка нескольких видео одним воркером (один воркер = одна
  задача за раз; для масштаба — несколько воркеров).
- Изменение CV-логики (используется как есть).
