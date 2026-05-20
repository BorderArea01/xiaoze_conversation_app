# WebSocket Device Telemetry Test

This folder contains standalone test payloads and a sender script for:

```text
ws://192.168.2.236:8085/ws/device/telemetry
```

The messages are sent as WebSocket text frames containing JSON.

## Apifox Manual Test

1. Create a WebSocket request.
2. Connect to:

```text
ws://192.168.2.236:8085/ws/device/telemetry
```

3. Copy one of these payloads into the message box:

- `payloads/reachy_mini_wireless.json`
- `payloads/reachy_mini_conversation_laptop.json`
- `payloads/reachy_mini_simulation.json`

4. Send it. A successful response currently looks like:

```json
{"code":200}
```

## Script Test

From the project root:

```bash
uv run python ws_device_telemetry_test/send_telemetry.py
```

Send a specific payload:

```bash
uv run python ws_device_telemetry_test/send_telemetry.py --payload ws_device_telemetry_test/payloads/reachy_mini_wireless.json
```

Send three messages, one per second:

```bash
uv run python ws_device_telemetry_test/send_telemetry.py --count 3 --interval 1
```

Use another server:

```bash
uv run python ws_device_telemetry_test/send_telemetry.py --url ws://192.168.2.236:8085/ws/device/telemetry
```

The script refreshes `timestamp` to the current millisecond timestamp before every send.
