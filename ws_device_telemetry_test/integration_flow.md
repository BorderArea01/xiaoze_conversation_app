# Reachy Mini Platform Integration Flow

This document summarizes the current platform integration findings before building the Reachy Mini app integration.

## Environment

Platform host:

```text
192.168.2.236
```

Confirmed ports:

```text
8088: HTTP activation API and terminal WebSocket
8085: telemetry WebSocket works; terminal WebSocket returns HTTP 401
```

Current activated device unique identifier:

```text
reachy-mini-001
```

Current activated platform server id:

```text
2056652440241684481
```

Current user id used for terminal tests:

```text
1999
```

## 1. Device Activation

Endpoint:

```http
POST http://192.168.2.236:8088/system/serverConfig/deviceActivate
```

Headers:

```http
Content-Type: application/json
authorization: Bearer <login-token>
```

Request body:

```json
{
  "activationCode": "zxomP9",
  "deviceName": "Reachy Mini Wireless",
  "deviceTypeName": "小泽机器人",
  "deviceId": "reachy-mini-001"
}
```

Observed successful response:

```json
{
  "msg": "操作成功",
  "code": 200,
  "data": {
    "serverId": "2056652440241684481",
    "activationTime": "2026-05-19T16:25:27.000+08:00"
  }
}
```

Important notes:

- The activation endpoint failed on port `8085` with `401 Unauthorized`.
- The same endpoint succeeded on port `8088`.
- `deviceTypeName` was changed from `轮足机器人` to `小泽机器人`.
- `deviceId` should remain the stable unique identifier `reachy-mini-001`.
- The returned `serverId` is useful for platform records, but the terminal WebSocket binding check passed only after using `reachy-mini-001` as the terminal path/device id.

## 2. Telemetry WebSocket

Endpoint:

```text
ws://192.168.2.236:8085/ws/device/telemetry
```

Message format:

```json
{
  "deviceId": "reachy-mini-001",
  "deviceName": "Reachy Mini Wireless",
  "deviceSn": "RMW-2026-001",
  "enterpriseId": "1001",
  "enterpriseName": "Pollen Robotics Lab",
  "tenantId": "reachy-mini-demo",
  "hostName": "reachy-mini-wireless-01",
  "ipAddress": "192.168.2.42",
  "osName": "Ubuntu 22.04",
  "architecture": "aarch64",
  "timestamp": 1779180000000,
  "deviceInfo": {
    "cpuUsage": 38.7,
    "memoryTotalMb": 8192,
    "memoryUsedMb": 3560
  },
  "metrics": {
    "loadAverage": 1.24,
    "conversationBackend": "openai_compatible_chat",
    "activeProfile": "default",
    "cameraEnabled": true,
    "headTracker": "mediapipe",
    "batteryPercent": 86.5,
    "audioInputSampleRate": 16000,
    "audioOutputSampleRate": 24000
  },
  "gpus": []
}
```

Observed response:

```json
{"code":200}
```

Existing local test assets:

```text
ws_device_telemetry_test/payloads/reachy_mini_wireless.json
ws_device_telemetry_test/payloads/reachy_mini_conversation_laptop.json
ws_device_telemetry_test/payloads/reachy_mini_simulation.json
ws_device_telemetry_test/send_telemetry.py
```

Run telemetry test:

```bash
uv run python ws_device_telemetry_test/send_telemetry.py --count 3 --interval 1
```

## 3. Terminal WebSocket For ASR Signal

Expected terminal WebSocket:

```text
ws://192.168.2.236:8088/websocket/terminal/reachy-mini-001
```

Headers:

```http
authorization: Bearer <login-token>
```

Current simplified message:

```json
{
  "type": "ASR_SIGNAL",
  "data": {
    "user_id": "1991",
    "session_id": "session-20260519-001",
    "query": "帮我查询今天的访客登记情况"
  }
}
```

Observed behavior:

- `ws://192.168.2.236:8085/websocket/terminal/...` returns `HTTP 401` during the WebSocket handshake.
- `ws://192.168.2.236:8088/websocket/terminal/MAC-001` connects, then closes with `1008 policy violation: 设备未绑定数字员工`.
- `ws://192.168.2.236:8088/websocket/terminal/2056652440241684481` connects, then closes with `1008 policy violation: 设备未绑定数字员工`.
- `ws://192.168.2.236:8088/websocket/terminal/reachy-mini-001` connects and accepts messages without closing.
- After binding, sending simplified `ASR_SIGNAL` messages on the `reachy-mini-001` terminal path returns streamed `TTS_MESSAGE` content.

Existing local terminal test:

```text
ws_device_telemetry_test/terminal_chat_probe.py
```

Run terminal test:

```bash
uv run python ws_device_telemetry_test/terminal_chat_probe.py
```

## 4. Confirmed Identifier Rules

Use this for activation request body:

```text
deviceId = reachy-mini-001
```

Use this for terminal WebSocket path:

```text
/websocket/terminal/reachy-mini-001
```

Use this for terminal message body:

```text
data.user_id = 1991
data.session_id = session-20260519-001
data.query = <conversation text>
```

Context continuity depends on whether `session_id` stays the same.

Do not use these as the terminal path unless the backend changes its binding rules:

```text
MAC-001
2056652440241684481
```

The platform `serverId` returned by activation should be stored for records, but it did not work as the terminal WebSocket identifier in current tests.

## 5. Current Open Questions

Before Reachy Mini app development, confirm with backend:

1. Is `user_id = 1991` the production user id or only a test value?
2. Should `session_id` be generated per user session, per app start, or manually controlled?
3. Does platform expect playback-complete or action-complete events after TTS finishes?
4. Is there a heartbeat/ping message required after connection establishment?

## 6. Suggested Reachy Mini App Integration Plan

### Phase 1: Platform Client Module

Create a small platform client inside the Reachy Mini app:

- `activate_device()`
- `send_telemetry()`
- `connect_terminal()`
- `send_asr_signal(query)`
- `handle_terminal_message(message)`

Keep this module independent from robot motion and conversation logic.

### Phase 2: Telemetry Heartbeat

Periodically report:

- device id/name/sn
- hostname/ip/os/architecture
- CPU/memory
- conversation backend
- active profile
- camera/head-tracking status
- robot connection status
- optional battery/status values if available

Suggested interval:

```text
5-30 seconds during development
30-60 seconds in production
```

### Phase 3: Terminal Message Bridge

Use the terminal WebSocket to send text-level user queries first:

```text
Reachy Mini local speech/ASR -> ASR_SIGNAL query -> platform
```

Once backend response format is confirmed:

```text
platform response -> Reachy Mini TTS/speech output -> optional robot motion/tool call
```

### Phase 4: Realtime App Behavior

After terminal responses are confirmed, decide whether platform or local app owns:

- ASR
- LLM reasoning
- TTS
- robot motion/tool execution

If platform owns LLM/TTS, Reachy Mini app becomes a device client.
If local app owns LLM/TTS, platform terminal WS can be used mainly for telemetry, commands, and event logging.

## 7. Known Good Commands

Activate device:

```powershell
$headers = @{ authorization = "Bearer <login-token>" }
$body = @{
  activationCode = "zxomP9"
  deviceName = "Reachy Mini Wireless"
  deviceTypeName = "小泽机器人"
  deviceId = "reachy-mini-001"
} | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "http://192.168.2.236:8088/system/serverConfig/deviceActivate" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

Send telemetry:

```bash
uv run python ws_device_telemetry_test/send_telemetry.py
```

Probe terminal chat:

```bash
uv run python ws_device_telemetry_test/terminal_chat_probe.py
```
