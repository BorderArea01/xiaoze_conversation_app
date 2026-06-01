# Reachy Mini 平台接口联调流程

这份文档用于确认当前平台接口联调结果，后续会基于这些结论开发 Reachy Mini app。

## 1. 基础信息

平台地址：

```text
192.168.2.236
```

已确认端口：

```text
8088：设备激活 HTTP 接口、终端 WebSocket 接口
8085：设备遥测 WebSocket 接口；终端 WebSocket 在该端口返回 HTTP 401
```

Reachy Mini 唯一标识：

```text
reachy-mini-001
```

设备激活后返回的 serverId：

```text
2056652440241684481
```

当前终端测试使用的 user_id：

```text
1999
```

## 2. 设备激活接口

接口：

```http
POST http://192.168.2.236:8088/system/serverConfig/deviceActivate
```

请求头：

```http
Content-Type: application/json
authorization: Bearer <login-token>
```

请求体：

```json
{
  "activationCode": "zxomP9",
  "deviceName": "Reachy Mini Wireless",
  "deviceTypeName": "小泽机器人",
  "deviceId": "reachy-mini-001"
}
```

已成功返回：

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

结论：

- 设备激活接口要走 `8088`。
- `8085` 会返回 `401 Unauthorized`。
- `deviceTypeName` 使用 `小泽机器人`。
- `deviceId` 使用稳定唯一标识 `reachy-mini-001`。
- 返回的 `serverId` 可以保存，但目前终端 WS 绑定校验使用的是 `reachy-mini-001`，不是 `serverId`。

## 3. 设备遥测 WebSocket

接口：

```text
ws://192.168.2.236:8085/ws/device/telemetry
```

示例消息：

```json
{
  "deviceId": "reachy-mini-001",
  "deviceName": "Reachy Mini Wireless",
  "deviceSn": "RMW-2026-001",
  "enterpriseId": "1001",
  "enterpriseName": "Reachy Mini Test Lab",
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

已观察到成功响应：

```json
{"code":200}
```

本地测试文件：

```text
ws_device_telemetry_test/send_telemetry.py
ws_device_telemetry_test/payloads/reachy_mini_wireless.json
ws_device_telemetry_test/payloads/reachy_mini_conversation_laptop.json
ws_device_telemetry_test/payloads/reachy_mini_simulation.json
```

运行测试：

```bash
uv run python ws_device_telemetry_test/send_telemetry.py --count 3 --interval 1
```

## 4. 终端 WebSocket / ASR_SIGNAL

当前可建立连接的接口：

```text
ws://192.168.2.236:8088/websocket/terminal/reachy-mini-001
```

请求头：

```http
authorization: Bearer <login-token>
```

当前简化消息体：

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

上下文关联只取决于 `session_id` 是否一致；设备身份由 WebSocket 路径 `/websocket/terminal/reachy-mini-001` 表达。

已测试结果：

- `ws://192.168.2.236:8085/websocket/terminal/...`：握手阶段返回 `HTTP 401`。
- `ws://192.168.2.236:8088/websocket/terminal/MAC-001`：能连接，但关闭原因是 `设备未绑定数字员工`。
- `ws://192.168.2.236:8088/websocket/terminal/2056652440241684481`：能连接，但关闭原因是 `设备未绑定数字员工`。
- `ws://192.168.2.236:8088/websocket/terminal/reachy-mini-001`：能连接，能发送消息，不再提示未绑定。
- 按新版消息体发送 `ASR_SIGNAL` 后，服务端会流式返回 `TTS_MESSAGE`。
- 正文分片在 `data.type == "0"` 的 `data.text` / `data.content` 中。
- `data.type == "2"` 是状态消息，例如 `开始推理`、`推理结束`。
- `推理结束` 可作为当前轮对话的结束标记。

本地测试脚本：

```text
ws_device_telemetry_test/terminal_chat_probe.py
```

运行测试：

```bash
uv run python ws_device_telemetry_test/terminal_chat_probe.py
```

## 5. 当前确认的 ID 使用规则

设备激活 body：

```text
deviceId = reachy-mini-001
```

终端 WebSocket 路径：

```text
/websocket/terminal/reachy-mini-001
```

终端 WebSocket 消息体：

```text
data.user_id = 1991
data.session_id = session-20260519-001
```

当前不要用于终端路径：

```text
MAC-001
2056652440241684481
```

`2056652440241684481` 是激活返回的 serverId，可以保存到本地配置，但目前不能作为终端 WS 路径标识使用。

## 6. 待后端确认的问题

终端 WS 新版消息体已经能流式返回内容。继续开发前还需要确认：

1. `user_id` 是否会在生产环境固定，还是每个用户不同。
2. `session_id` 的生成和更新规则，是否按用户会话、按设备会话或按 app 启动周期。
3. `TTS_MESSAGE` 中是否会返回机器人动作/设备控制指令。
4. 平台是否希望 Reachy Mini 回传播放完成、动作完成等状态事件。
5. 建立连接后是否需要发送 heartbeat/ping。

## 7. Reachy Mini App 开发建议

当前已完成第一版 app 集成骨架：

```text
src/xiaoze_conversation_app/platform_client.py
```

主 app 启动时会按环境变量决定是否启用平台服务。启用后会在后台线程中周期性发送遥测；如果配置了启动激活，也会先调用设备激活接口。

同时已新增 `platform_agent` 后端。这个后端会让平台智能体成为 Reachy Mini 对话核心：

```text
Reachy Mini 麦克风 -> 本地 ASR -> 平台 ASR_SIGNAL -> 平台流式 TTS_MESSAGE -> 本地 TTS -> Reachy Mini 播放
```

启用方式：

```env
REACHY_PLATFORM_ENABLED=1
REACHY_PLATFORM_TOKEN=<login-token>
REACHY_PLATFORM_ACTIVATE_ON_START=0
REACHY_PLATFORM_DEVICE_ID=reachy-mini-001
REACHY_PLATFORM_DEVICE_NAME=Reachy Mini Wireless
REACHY_PLATFORM_DEVICE_TYPE_NAME=小泽机器人
REACHY_PLATFORM_USER_ID=1991
REACHY_PLATFORM_SESSION_ID=session-20260519-001
BACKEND_PROVIDER=platform_agent
REACHY_PLATFORM_TELEMETRY_INTERVAL_S=30
```

默认平台地址：

```env
REACHY_PLATFORM_HTTP_BASE_URL=http://192.168.2.236:8088
REACHY_PLATFORM_TELEMETRY_WS_URL=ws://192.168.2.236:8085/ws/device/telemetry
REACHY_PLATFORM_TERMINAL_WS_URL=ws://192.168.2.236:8088/websocket/terminal/reachy-mini-001
```

### 阶段一：平台 Client 模块

先做独立模块，不和机器人动作逻辑耦合：

```text
activate_device()
send_telemetry()
connect_terminal()
send_asr_signal(query)
handle_terminal_message(message)
```

### 阶段二：遥测心跳

周期性上报：

- 设备 ID、设备名、SN
- 主机名、IP、系统、架构
- CPU、内存
- Reachy Mini 连接状态
- 当前 conversation backend
- 当前 profile
- 摄像头/头部追踪状态
- 可选电量、动作状态、音频采样率

开发阶段建议 5-30 秒一次，生产环境可以 30-60 秒一次。

### 阶段三：终端消息桥接

先接文字级链路：

```text
Reachy Mini 本地识别/文本输入 -> ASR_SIGNAL -> 平台
```

等平台响应协议确认后，再接：

```text
平台响应 -> Reachy Mini TTS/说话 -> 可选动作/工具调用
```

### 阶段四：决定智能逻辑归属

需要确认最终架构：

- 如果平台负责 ASR/LLM/TTS，Reachy Mini app 主要做设备客户端、播放、动作执行。
- 如果本地 app 负责 ASR/LLM/TTS，平台主要做设备管理、遥测、指令和事件记录。

## 8. 已知可用命令

设备激活：

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

遥测测试：

```bash
uv run python ws_device_telemetry_test/send_telemetry.py
```

终端 ASR_SIGNAL 测试：

```bash
uv run python ws_device_telemetry_test/terminal_chat_probe.py
```
