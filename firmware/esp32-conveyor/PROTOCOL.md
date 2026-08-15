# Device sync protocol v1

ESP32 выполняет исходящий запрос:

```http
POST https://asyl-ltd.kz/api/conveyors/v1/device/sync/
Authorization: Device <canonical-public-uuid>.<43-char-base64url-token>
Content-Type: application/json
Accept: application/json
```

TLS проверяется через ESP certificate bundle и hostname verification. Redirect
не выполняется.

## Request

```json
{
  "protocol_version": 1,
  "boot_id": "f6b1202e-815d-4f87-8a23-71e12e86b739",
  "seq": 17,
  "ack_revision": 42,
  "output_state": 1,
  "feedback_state": 1,
  "fault": null,
  "uptime_ms": 18304,
  "wifi_rssi": -57,
  "firmware": "1.0.0"
}
```

- `boot_id` — новый random UUIDv4 при каждой загрузке;
- `seq` начинается с 1 и строго растёт внутри `boot_id`;
- `ack_revision` равен `null` до первой durable принятой revision;
- `output_state` — фактически установленная программой команда GPIO `0|1`;
- `feedback_state` — независимый физический вход `0|1`;
- `fault` — `null` либо стабильный machine-readable код.

## Response

ON:

```json
{
  "protocol_version": 1,
  "server_time": 1786755600,
  "next_sync_ms": 500,
  "command": {
    "revision": 43,
    "state": 1,
    "lease_ms": 1500,
    "session_id": 912,
    "target_total": 500,
    "reason": "active_session"
  }
}
```

OFF:

```json
{
  "protocol_version": 1,
  "server_time": 1786755601,
  "next_sync_ms": 500,
  "command": {
    "revision": 44,
    "state": 0,
    "lease_ms": 0,
    "session_id": null,
    "target_total": null,
    "reason": "target_reached"
  }
}
```

JSON строгий: все показанные поля присутствуют ровно один раз, неизвестные
поля и type coercion отклоняются. `state` — именно JSON integer `0|1`, не bool
и не строка. `server_time` — Unix epoch seconds integer. `next_sync_ms` —
100..500. Для ON: `revision/session_id/target_total >= 1`, `lease_ms=1..1500`.
Для OFF: `lease_ms=0`, `session_id=null`, `target_total=null`.

## Backend invariants

1. `revision` монотонна для одного device и меняется при изменении смысла
   команды. Одинаковая ON revision используется только как lease renewal.
2. При новом `boot_id` backend сначала возвращает OFF. Только после ACK этого
   OFF он может создать более новую ON revision. Старая OFF-команда физически
   снимает выход, но не засчитывается как post-boot handshake.
3. После `fault != null`, feedback mismatch или отсутствия heartbeat backend
   защёлкивает новую OFF revision. ESP очищает локальный fault только получив
   эту более новую OFF и отдельный физический feedback OFF; неудачная ON
   revision остаётся заблокированной. Повторный запуск требует новой сессии и
   ещё более новой ON revision.
4. Backend отклоняет повтор/уменьшение `(boot_id, seq)` и не принимает
   `ack_revision`, которой не выдавал этому устройству.
5. Ответ создаётся заново на каждый sync и содержит актуальный `server_time`.

Replay старой HTTPS-команды не продлевает привод: persisted revision,
boot-handshake, session binding и локальный lease работают совместно. При
ошибке JSON/времени/revision ESP32 остаётся либо переходит в OFF.
