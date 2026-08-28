# Лаборатория CV-моделей

Раздел `/management/model-tests` предназначен только для Django-superuser и
проверяет заранее установленные на camera-PC наборы моделей на локальном видео.
Браузер никогда не получает `AI_SERVICE_API_KEY`, путь к checkpoint или прямой
адрес CV-сервиса.

## Публичный контракт Asyl

Все маршруты требуют JWT superuser:

- `GET /api/cameras/model-tests/` — доступные bundle, readiness, defaults,
  лимиты и состояние production-процессоров;
- `POST /api/cameras/model-tests/?bundle=...&line=...&direction=...&inference_fps=...`
  — raw MP4/MOV/AVI/MKV body с точным `Content-Length`, ответ `202` с `job_id`;
- `GET /api/cameras/model-tests/<uuid>/?after_event=0&limit=100` — состояние,
  прогресс, итоговая статистика и страницы crossing events.

Django валидирует только allowlisted bundle ID и параметры, затем потоково
передаёт байты в `/model-tests` camera-PC чанками до 1 MiB. Произвольные `.pt`
файлы через этот интерфейс загрузить нельзя. Результат имеет
`Cache-Control: no-store`.

## Что обновляется в реальном времени

Текущий CV-контракт публикует во время `queued/running` процент и число
decoded/processed кадров. Crossing events, bbox и distributions появляются
атомарно после `completed`; аннотированное видео сервис не создаёт. Поэтому UI
показывает live upload/progress, а после расчёта позволяет перейти к событию в
локальном исходном видео и наложить bbox. Для live bbox нужен отдельный
инкрементальный контракт camera-PC — backend не синтезирует отсутствующие данные.

Jobs хранятся только в памяти camera-PC (обычно TTL 1 час) и исчезают после его
рестарта. История сравнения в текущем UI живёт только до перезагрузки страницы.

## Настройка

Asyl backend:

```env
AI_SERVICE_URL=http://camera-pc:8890
AI_SERVICE_API_KEY=<backend-only plaintext key>
AI_MODEL_TEST_MAX_UPLOAD_BYTES=536870912
AI_MODEL_TEST_UPLOAD_TIMEOUT=600
```

Camera-PC должен отдельно включить `AI_VIDEO_TEST_ENABLED=1` и настроить
`AI_VIDEO_TEST_BUNDLES_JSON`. Production bundle использует установленные
`detector.pt`, `color_classifier.pt`, `brand_classifier.pt`; candidate bundle
добавляется только через доверенный серверный allowlist.

Nginx имеет отдельный location с `client_max_body_size 512m`, выключенным
`proxy_request_buffering` и 10-минутными upload timeouts. При изменении Django
лимита синхронно обновите nginx. По умолчанию camera-PC отклоняет тест с `409`,
если работают production camera processors: интерфейс это показывает и никогда
не останавливает камеры автоматически.

## Проверка и откат

1. `GET /api/cameras/model-tests/` под superuser возвращает `enabled=true` и
   хотя бы один `ready=true` bundle.
2. Загрузить короткий MP4, увидеть upload progress, затем `queued/running` и
   `completed` с summary/events.
3. Обычный сотрудник получает `403` на GET/POST/detail.
4. В DevTools нет `X-Api-Key` и запросов напрямую к порту camera-PC.

Откат UI/API безопасен обычным rollback релиза Asyl. Экстренный kill switch —
`AI_VIDEO_TEST_ENABLED=0` на camera-PC; production counting от этого не меняется.
