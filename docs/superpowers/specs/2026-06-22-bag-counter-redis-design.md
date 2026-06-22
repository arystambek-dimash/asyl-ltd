# Счётчик мешков (Redis live-count) — Дизайн

**Дата:** 2026-06-22
**Статус:** согласован, готов к написанию плана реализации

## Цель

Подключить готовый CV-воркер (`integrations/cv_service_handoff`, считает мешки
по линии конвейера и отдаёт событие на каждый мешок) к CRM. Живой счёт мешков
ведётся **в Redis** (на каждый мешок `INCR`, без обращений к БД), а в базу
итог попадает **один раз** — при закрытии сессии оператором. Оператор на экране
«Счётчик мешков» видит живое число, вводит номер машины и завершает сессию;
итог уходит в заказ (`record_loading` → статус `loading`) и виден в «Пост
отгрузки».

MVP: конец загрузки и номер машины задаёт **оператор вручную** (без автодетекта
паузы/номера).

## Архитектура

```
CV-воркер ──+1──▶ Redis INCR (память + AOF на диск); БД НЕ трогаем
                   ключ: count:camera:<camera_pk>
CRM «Счётчик» ──GET──▶ читает счёт из Redis (поллинг ~1–2 c)
Оператор: номер машины + «Закончить сессию»
   └▶ сервер: итог из Redis
        → CountSession(camera, bags, order, status=closed)   ← ОДНА запись в БД
        → record_loading(order, bags)  → заказ в `loading`
        → DEL ключ Redis
```

**Принцип:** Redis = быстрый буфер живого счёта (ноль обращений к БД на
инкремент); БД = постоянное хранилище итогов (история отгрузок).

## 1. Инфраструктура: Redis

- Новый сервис `redis` в `docker-compose.yml` (образ `redis:7-alpine`), запуск
  с `--appendonly yes` (AOF — счёт переживает перезапуск контейнера) и
  именованным volume `redisdata`.
- Бэкенд: зависимость `redis>=5` (redis-py). Адрес из env
  `REDIS_URL` (default `redis://redis:6379/0`; локально `redis://localhost:6379/0`).
- Если Redis недоступен — счётчик деградирует понятной ошибкой 503 (не падает
  весь сервер).

## 2. Хранилище счёта: counter_store.py

`backend/webhooks/counter_store.py` — тонкая обёртка над Redis:
- `increment(camera_pk, by=1) -> int` — `INCR`/`INCRBY`, возвращает новое значение.
- `get(camera_pk) -> int` — текущее значение (0 если ключа нет).
- `reset(camera_pk) -> None` — `DEL` ключа.
- Ключ: `count:camera:{camera_pk}`. TTL не ставим (сессия закрывается явно).

## 3. Модель данных

- **CountSession** (БД, пишется только при закрытии):
  `camera` FK→Camera, `bags` (int, итог), `order` FK→Order (null),
  `status` (`closed`; запись создаётся уже закрытой в MVP),
  `created_at`, `closed_at`, `closed_by` FK→User null.

Живой счёт в БД НЕ хранится — он в Redis. CountSession — только результат.

## 4. API

- **Инкремент (от CV-воркера):** `POST /api/webhook/camera/` для камеры типа
  `counter` с телом `{ "camera_id": "...", "increment": 1 }` (или
  `"bags": N` для пакетного добавления) → `counter_store.increment` → ответ по
  шаблону камеры с `{{bags}}` = текущий счёт. **Заказ не трогается** (это не
  финальная загрузка). Ключевое отличие от прежней логики counter: наличие
  `increment` → режим живого счёта; без него (как раньше) — финальная загрузка.
- **Текущий счёт (для CRM):** `GET /api/count/{camera_pk}/` →
  `{ "camera": id, "camera_name": "...", "bags": N }`. Право `cameras.view`.
- **Закрыть сессию (оператор):** `POST /api/count/{camera_pk}/close/`
  body `{ "plate": "777ABC02" }`. Логика (транзакция):
  1. `bags = counter_store.get(camera_pk)`.
  2. найти заказ по `truck_number` (нормализация номера).
  3. `record_loading(order, bags, user)` → статус `loading` (сервис сам
     проверяет, что заказ в `arrived`).
  4. создать `CountSession(camera, bags, order, status=closed, closed_by)`.
  5. `counter_store.reset(camera_pk)`.
  6. вернуть `{ "bags": N, "order_id": ..., "status": "loading" }`.
  При ошибке (нет заказа / неверный статус) — `400 {"detail","code"}`, счёт в
  Redis НЕ сбрасывается (оператор поправит номер и повторит). Право
  `cameras.manage`.
- **Список сессий (история):** `GET /api/count-sessions/?camera=` (read,
  `cameras.view`) — для статистики.

## 5. Фронт: экран «Счётчик мешков»

Раздел в сайдбаре (под «Управление», право `cameras.view`):
`/management/counter`.
- Выбор камеры-счётчика (если их несколько).
- **Большое живое число** мешков — поллинг `GET /api/count/{id}/` каждые ~1.5 c.
- Поле **номер машины** (компонент `LicensePlateInput`) + кнопка **«Закончить
  сессию»** (право `cameras.manage`).
- После закрытия: тост/сообщение «N мешков записано в заказ #X», счётчик
  сбрасывается на 0, статистика появляется в «Пост отгрузки» (там уже есть
  `bags_loaded`).
- История последних сессий камеры (таблица: время, мешков, заказ).

## 6. CV-адаптер

`integrations/bag_counter_client.py` — тонкий мост, CV-код не трогает:
```
counter = BagColorCounter(det_weights=..., cls_weights=..., camera_id=..., ...)
for event in counter.run(source):
    requests.post(WEBHOOK_URL,
                  json={"camera_id": CAMERA_ID, "increment": 1},
                  headers={"X-Camera-Key": CAMERA_KEY}, timeout=2)
```
Один мешок (`event`) = один `+1`. README обновляется примером.

## 7. Целостность, ошибки, тестирование

### Целостность
- Инкремент НЕ пишет в БД (только Redis) — БД не нагружается потоком событий.
- Закрытие сессии — в транзакции (CountSession + record_loading атомарно); при
  ошибке счёт Redis сохраняется.
- Redis с AOF + volume — счёт переживает перезапуск контейнера.

### Ошибки
- Redis недоступен → 503 с понятным сообщением (счётчик), сервер не падает.
- Закрытие без заказа/в неверном статусе → 400, счёт не сброшен.

### Тестирование (TDD)
- `counter_store`: increment/get/reset (на fakeredis или реальном Redis в тесте).
- Вебхук counter с `increment` → счёт растёт в Redis, заказ НЕ меняется.
- `GET /count/{id}/` отдаёт текущее число.
- close: заказ `arrived` + номер → `loading` + bags записаны + CountSession
  создана + Redis сброшен.
- close при отсутствии заказа → 400, Redis не сброшен.
- Права: view для счёта, manage для close.

## Вне рамок (YAGNI)

- Автодетект конца загрузки (по паузе) и авточтение номера — позже.
- Реалтайм через WebSocket (поллинг достаточно для MVP).
- Учёт цвета/веса по событию (CountSession хранит только число мешков; цвет —
  позже, модель его уже отдаёт).
