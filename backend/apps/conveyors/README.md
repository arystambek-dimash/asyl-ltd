# Cloud conveyor control

This app is the public HTTPS broker between the order-bound AI session and an
ESP32. The server cannot open a connection to an ESP32 behind NAT: the device
must make an outbound sync request every 500 ms and treat every error as OFF.

## One device per camera

The durable device binding is the single source of truth.  If a camera has an
active `ConveyorDevice`, every new order session on that camera uses that ESP32.
If it has no active device, the existing camera-PC/direct path is unchanged.
There is no separate server-side camera transport map to maintain.

The camera PC still uses a separate OFF-only observation credential. Configure
its raw token as `AI_CONVEYOR_CLOUD_API_KEY` and store only its SHA-256 digest in
`CONVEYOR_AI_CALLBACK_TOKEN_SHA256`. Never keep a camera-PC Modbus output
configured for a camera that is bound to an ESP32; the edge service rejects an
attempt to run both transports at once.

The selected transport is frozen into every order session. Bind, rebind, or
disable devices only after open sessions are stopped and physical OFF is
confirmed. A binding change never migrates or resumes an already-open session
through another master.

## Device enrollment

A superuser creates a controller with `POST /api/conveyors/devices/`:

```json
{"name":"Line 2 ESP32","camera_source":"cam2","is_active":true}
```

The response contains `credential.token` and `credential.authorization` once.
Only a SHA-256 digest of the 256-bit token is stored. GET/list/status responses
never contain the token or digest. The admin can call:

- `POST /api/conveyors/devices/<uuid>/rotate-secret/` (returns the new token once);
- `POST /api/conveyors/devices/<uuid>/emergency-stop/` (new terminal OFF revision);
- `POST /api/conveyors/devices/<uuid>/disable/` (OFF plus credential revocation).

Rotation and disable are fail-OFF. If the old device cannot fetch the new OFF
revision, its last ON lease expires locally within at most 1500 ms.

## ESP32 sync protocol

`POST /api/conveyors/v1/device/sync/`

```http
Authorization: Device <public-uuid>.<token>
Content-Type: application/json
```

```json
{
  "protocol_version": 1,
  "boot_id": "11111111-1111-4111-8111-111111111111",
  "seq": 42,
  "ack_revision": 9,
  "output_state": 1,
  "feedback_state": 1,
  "fault": null,
  "uptime_ms": 21000,
  "wifi_rssi": -61,
  "firmware": "1.0.0"
}
```

All numbers are strict JSON integers: booleans and numeric strings are rejected.
`seq` is strictly increasing per `boot_id` and is limited to `2^63-1`. The
output is the ESP GPIO/latch; feedback must come from a separate contactor
auxiliary input. A changed boot ID always creates a newer terminal OFF and can
never resume an old ON command.

```json
{
  "protocol_version": 1,
  "server_time": 1786752000,
  "next_sync_ms": 500,
  "command": {
    "revision": 10,
    "state": 1,
    "lease_ms": 1200,
    "session_id": 321,
    "target_total": 500,
    "reason": "active_session"
  }
}
```

ON always has a positive lease no greater than 1500 ms and a bound session and
target. OFF always has `lease_ms=0`, `session_id=null`, and
`target_total=null`. The ESP must allow only one in-flight request, apply only
monotonic revisions, give OFF priority, use a local monotonic lease deadline,
and switch OFF on timeout, HTTP error, invalid JSON, TLS failure, reboot or
Wi-Fi loss. The server never promises an uninterrupted Wi-Fi connection.

## Camera AI observation

The camera PC uses a separate raw callback token whose SHA-256 digest is stored
on Django. It sends at most two updates per second, plus retries the terminal
target event until a 2xx response:

`POST /api/conveyors/v1/ai/observation/`

```http
Authorization: Bearer <camera-callback-token>
```

```json
{
  "protocol_version": 1,
  "camera": "cam2",
  "session_id": 321,
  "target_total": 500,
  "edge_boot_id": "22222222-2222-4222-8222-222222222222",
  "seq": 84,
  "total": 500,
  "terminal_reason": "target_reached"
}
```

The endpoint is OFF-only: it may refresh the AI watchdog, advance the monotonic
count or terminally stop, but it can never set ON. `total >=` the frozen session
target always increments the OFF revision. Exact duplicate observations are
idempotent without refreshing freshness; changed/reordered payloads are
rejected. Accepted terminal reasons are defined in `serializers.py`.

Software leases supplement rather than replace the physical E-stop, overload
protection, contactor interlock, boot-default OFF and a local hardware watchdog.
