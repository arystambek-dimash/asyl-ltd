# Asyl-LTD CRM — запуск и рабочий флоу (для мощного компа / агента)

Этот документ — пошаговая инструкция, чтобы поднять систему на машине с GPU и
прогнать полный цикл отгрузки с подсчётом мешков по видео и живым превью.

---

## 1. Что есть в системе

- **CRM** (Docker): PostgreSQL + Redis + Django backend + Next.js frontend.
- **CV-воркер** (вне Docker, на GPU): берёт видео из очереди CRM, считает мешки
  готовой моделью (`cv_service_handoff`), шлёт `+1` в Redis на каждый мешок и
  размеченные кадры (боксы + линия) для живого превью.

CV-зависимости (torch/ultralytics/opencv, ~3 ГБ) НЕ в Docker — поэтому воркер
запускается отдельно.

---

## 2. Рабочий флоу заказа (текущий)

```
draft → confirmed → paid → arrived → loading → loaded → shipped
```

1. **Менеджер** создаёт заказ, вписывает **номер КАМАЗа** заранее (это ключ
   привязки на всех этапах).
2. **Оплата** — базовая проверка (оплачен/нет показывается; жёстких ворот пока нет).
3. **Прибытие** — камера на въезде шлёт номер + вес въезда (вебхук `entry`),
   заказ → `arrived`. На демо без датчика — оператор вводит вес въезда вручную
   (fallback-поле). Номер оператор не вводит — он уже в заказе.
4. **Загрузка** — оператор в карточке («Пост отгрузки») жмёт «Загрузить видео».
   Это переводит заказ `arrived → loading` и ставит видео в очередь.
5. **CV-воркер** забирает задачу, считает мешки. Оператор видит **живой поток с
   разметкой** + растущее число мешков.
6. Когда подсчёт окончен — оператор жмёт **«Загрузка завершена»** (`→ loaded`).
7. **Выезд** — оператор вводит вес выезда. Система сравнивает:
   `(выезд − въезд)` = вес груза vs `(мешки × вес мешка)` = ожидание.
   Расхождение — предупреждение, отгрузку не блокирует. Заказ → `shipped`,
   склад списывается.

Привязка по номеру: вебхуки `entry`/`counter`/`exit` находят заказ по совпадению
номера. Нераспознанный номер → запись в журнал вебхуков (`deny`).

---

## 3. Шаг A — поднять CRM (Docker)

В корне репозитория:

```bash
docker compose up --build -d
```

Поднимет 4 сервиса (db, redis, backend, frontend). Миграции применяются
автоматически. Проверка:

```bash
docker compose ps                 # все 4 — running
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/    # бэкенд жив
```

- Фронт: http://localhost:3000
- API: http://localhost:8000/api/
- Логин супер-админа задаётся через env `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASS`
  (по умолчанию `admin@asyl.local` / `admin12345`).

Остановить: `docker compose down` (данные сохраняются в томах
`pgdata`/`redisdata`/`mediadata`).

---

## 4. Шаг B — получить ключ камеры-счётчика

Воркер аутентифицируется ключом активной камеры типа **counter**.

1. Фронт → **Управление → Камеры → Добавить камеру**, тип «Счётчик загрузки»,
   статус активна. Скопируй показанный ключ (`api_key`).
2. Либо из shell контейнера:

```bash
docker compose exec backend python manage.py shell -c "
from webhooks.models import Camera
c = Camera.objects.filter(kind='counter', status='active').first()
print('camera_id =', c.camera_id, '| key =', c.api_key)
"
```

> Важно: если активных counter-камер несколько, загрузка видео привязывает
> задачу к **первой** (`.first()`). Чтобы не угадывать — держи ровно одну
> активную counter-камеру.

---

## 5. Шаг C — подготовить CV-окружение (один раз, на GPU)

Нужен пакет `cv_service_handoff` (с `bag_pipeline.py`, `bag_counter.py` и
`weights/detector.pt`, `weights/color_classifier.pt`).

**Пакет должен содержать метод `run_annotated()`** в `bag_pipeline.py` (рядом с
`run()`) — он отдаёт размеченные кадры для живого превью. Если в твоей копии его
нет, добавь его (см. `integrations/README.md` / историю — метод добавляется
рядом с `run()`, `run()` не меняется).

Установить зависимости в отдельный venv:

```bash
python3 -m venv .worker-venv
source .worker-venv/bin/activate
pip install --upgrade pip
# GPU (пример RTX 50xx, CUDA 12.8):
pip install --pre torch torchvision --index-url https://download.pytorch.org/whl/nightly/cu128
pip install ultralytics opencv-python requests
```

Для CPU-проверки (медленно, только короткие ролики): `pip install torch torchvision ...`.

---

## 6. Шаг D — запустить воркер

```bash
source .worker-venv/bin/activate

ASYL_BASE_URL=http://localhost:8000 \
ASYL_CAMERA_ID=<camera_id из шага B> \
ASYL_CAMERA_KEY=<key из шага B> \
CV_DIR=/полный/путь/к/cv_service_handoff \
CV_DEVICE=0 \
python integrations/video_worker.py
```

- `CV_DEVICE=0` — GPU; `CV_DEVICE=cpu` — CPU (для теста).
- Воркер печатает `[worker] polling ...` и ждёт задачи.
- Когда оператор загрузит видео, воркер скачает его, посчитает мешки, в карточке
  появится живой поток с разметкой + число; в конце → `complete`.

---

## 7. Шаг E — прогнать цикл

1. Создай/возьми заказ со статусом до отгрузки, впиши номер КАМАЗа, оплати.
2. На посту отгрузки прими машину (введи вес въезда) → `arrived`.
3. Жми «Загрузить видео», выбери тестовый `.mp4`.
4. Смотри живой поток + счётчик (воркер должен быть запущен — иначе «В очереди»).
5. По окончании — «Загрузка завершена» → введи вес выезда → «Отгрузить».
6. Проверь блок сравнения веса и итоговый статус `shipped`.

---

## 8. Если видео висит «В очереди»

Это значит, **воркер не забрал задачу**. Проверь по порядку:

1. Воркер запущен и печатает `polling`?
2. `ASYL_CAMERA_KEY` — ключ **той** counter-камеры, к которой привязалась задача
   (загрузка берёт первую активную counter-камеру)?
3. Redis жив? `docker compose exec redis redis-cli ping` → `PONG`.
4. На CPU длинное видео считается очень долго (15–40 мин на 150-сек ролик) —
   возьми короткий ролик или GPU.

Вернуть зависшую задачу в очередь:

```bash
docker compose exec backend python manage.py shell -c "
from webhooks.models import VideoJob
j = VideoJob.objects.filter(status='processing').order_by('-id').first()
if j: j.status='queued'; j.started_at=None; j.save(); print('requeued', j.id)
"
```

---

## 9. Эндпоинты воркера (справочно)

| Метод | Путь | Назначение |
|-------|------|-----------|
| GET   | `/api/video-jobs/next/`            | взять следующую задачу (X-Camera-Key) |
| POST  | `/api/video-jobs/{id}/frame/`      | прислать размеченный JPEG (превью) |
| GET   | `/api/video-jobs/{id}/stream/`     | MJPEG-поток для UI (без auth) |
| POST  | `/api/webhook/camera/` `{increment:1}` | +1 в живой счётчик |
| POST  | `/api/video-jobs/{id}/complete/` `{bags:N}` | завершить, записать мешки |
| POST  | `/api/video-jobs/{id}/fail/`       | пометить ошибкой |

Превью необязательно: при недоступности Redis/сети счёт мешков продолжается.
