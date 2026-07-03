# Камеры цеха на дашборде — дизайн

Дата: 2026-07-03. Утверждено пользователем (вариант «go2rtc на сервере»).

## Задача

Показать живое видео с 8 камер цеха в блоке «Видеонаблюдение» на дашборде CRM.
Источник — MediaMTX на ПК с камерами (`100.109.156.107:8554`, RTSP, HEVC),
доступен prod-серверу по Tailscale. Браузеры HEVC не играют, поэтому нужен
серверный транскодинг.

## Архитектура

```
MediaMTX (RTSP HEVC, camNsub 640×360)
   │ Tailscale
   ▼
go2rtc (docker, prod) — ffmpeg HEVC→H.264 по требованию, MSE через WebSocket
   ▼
nginx /go2rtc/ (auth_request → Django) ──▶ браузер (MSE-плеер в CameraWall)
```

## Компоненты

### go2rtc (`deploy/go2rtc/go2rtc.yaml`, сервис в `docker-compose.prod.yml`)
- Образ `alexxit/go2rtc`, порт 1984 только внутри compose-сети.
- 8 потоков `cam1..cam8`: `ffmpeg:rtsp://${CAMERA_USER}:${CAMERA_PASS}@${CAMERA_HOST}:8554/camNsub#video=h264`.
- Реквизиты камер — в `.env` сервера (`CAMERA_HOST`, `CAMERA_USER`, `CAMERA_PASS`), не в git.
- Транскодинг запускается только пока есть зрители (on-demand).

### Django `apps/cameras` (без моделей)
- `GET /api/cameras/` — список камер (id, name, zone, src) для сотрудников
  (не `is_client`); единственный источник правды о списке камер.
- `POST /api/cameras/token/` — ставит короткоживущую подписанную HttpOnly-cookie
  `cam_token` (TimestampSigner, TTL 12ч) для доступа к стримам.
- `GET /api/cameras/auth/` — internal-эндпоинт для nginx `auth_request`:
  проверяет cookie, 204/403.

### nginx
- `location /go2rtc/` → proxy на go2rtc (HTTP+WebSocket), под `auth_request`.
- `location = /go2rtc/auth` — internal, субзапрос в Django.

### Фронтенд
- `camera-wall.tsx`: список камер берётся из `/api/cameras/`, на маунте
  запрашивается `/api/cameras/token/`.
- Новый компонент MSE-плеера: WebSocket на `wss://host/go2rtc/api/ws?src=camN`,
  протокол go2rtc MSE (fMP4 в MediaSource), авто-реконнект.
- Разметка/сетка CameraWall не меняется; при недоступности потока — прежний
  плейсхолдер «Нет сигнала».

## Ошибки и деградация
- Нет Tailscale/потока → go2rtc не отдаёт данные, тайл показывает «Нет сигнала».
- В dev без nginx/go2rtc — то же, ошибки фетча глотаются.
- Обрыв WebSocket → реконнект с бэкоффом.

## Ограничения / отложено
- Превью 360p (sub-потоки). Полное качество — после перевода NVR на H.264.
- Записи/архив, PTZ, доступ клиентам портала — вне скоупа.
