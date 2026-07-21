# AI-сервис камер

Windows-служба читает нормализованный RTSP-поток MediaMTX, выполняет детекцию
одной общей моделью `best.pt` и считает пересечения линии. H.264 поток
`cam<N>ai` публикуется только во время отгрузочной сессии; фоновый режим 24/7
работает без публикации и записи. На каждый активный `cam<N>` существует один
decoder, а inference всех камер проходит через единственный последовательный
worker общей модели.

> **Лицензия модели:** production-установка не меняет лицензионные права.
> До развёртывания нужно подтвердить, что проект выполняет AGPL-3.0 либо у
> организации есть подходящая коммерческая лицензия Ultralytics для закрытого
> внутреннего/edge-развёртывания.

## API

Все запросы требуют `X-Api-Key`. Query-параметр с ключом не поддерживается,
CORS и интерактивная документация отключены.

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/health` | модель, MediaMTX, processors и последние кадры |
| GET | `/cameras` | инвентарь MediaMTX и AI-состояния |
| GET | `/processors` | тёплые processors |
| GET | `/always-on` | выбранные фоновые processors и их состояние |
| PUT | `/always-on` | атомарно заменить постоянный список камер |
| DELETE | `/recordings` | удалить перечисленные сегменты записи с ПК камер |
| GET | `/processors/cam2` | состояние одной камеры |
| POST | `/processors/cam2/prewarm` | прогреть decoder/inference в `IDLE` без записи |
| POST | `/processors/cam2` | обнулить и перейти в `COUNTING` |
| POST | `/processors/cam2/reset` | обнулить текущий подсчёт |
| DELETE | `/processors/cam2` | заморозить результат и вернуться в `IDLE` |

`POST` принимает только безопасные настройки, никогда URL:

```json
{"source":"sub","line":"0,0.5,1,0.5","direction":"any"}
```

Camera ID строго соответствует `cam<N>`. Новый `POST` к уже считающему
processor идемпотентен и не сбрасывает результат. После `DELETE` обычная камера
остаётся прогретой в `IDLE`, а выбранная камера сразу возвращается в 24/7;
FFmpeg publisher в обоих случаях закрывается.

## Установка

На camera-PC применяется
[`deploy/camera-pc/install-ai-service.ps1`](../deploy/camera-pc/install-ai-service.ps1).
Он создаёт отдельный venv, копирует checkpoint, выполняет model warm-up,
проверяет MediaMTX и H.264 encoder, защищает файлы ACL, открывает порт 8890
только для Tailscale-IP backend и регистрирует boot-задачу `SYSTEM`.

На camera-PC сохраняется только `AI_SERVICE_API_KEY_SHA256`. Plaintext ключ
остаётся в `AI_SERVICE_API_KEY` production backend. Получить digest можно локально:

```powershell
python -c "import hashlib,os; print(hashlib.sha256(os.environ['AI_SERVICE_API_KEY'].encode()).hexdigest())"
```

Ручной запуск для диагностики из корня репозитория:

```powershell
.\.venv\Scripts\python.exe cv_service\ai_service.py
```

До вызова `uvicorn.run` сервис проверяет checkpoint/классы, выполняет warm-up,
выбирает `h264_nvenc`, `h264_qsv` или `libx264` и поднимает настроенные
`AI_PREWARM_CAMERAS`. При любой ошибке HTTP-порт не запускается.

## Постоянный подсчёт без фоновой записи

`PUT /always-on` принимает список `cam<N>` и атомарно сохраняет его в
`AI_ALWAYS_ON_STATE_PATH`. После перезапуска Windows-службы выбранные
процессоры восстанавливаются до открытия HTTP-порта. Они непрерывно читают
камеры и выполняют inference, но не публикуют `cam<N>ai`, поэтому MediaMTX не
создаёт фоновые видеозаписи.

При `POST /processors/cam<N>` прогретый процессор переключается в режим
отгрузочной сессии, обнуляет её счётчик и начинает публикацию/запись. `DELETE`
закрывает публикацию и сразу возвращает выбранную камеру в режим 24/7.

## Финализация

Финальный счётчик живёт в памяти camera-PC. Backend обязан выполнить в таком
порядке: `GET` snapshot → commit PostgreSQL → `DELETE`. Если DELETE не удался,
сессия остаётся открытой и завершение можно безопасно повторить; сохранённый
snapshot не теряется. Видеофайлы остаются в MediaMTX на camera-PC и удаляются
его 14-дневной retention-политикой.
