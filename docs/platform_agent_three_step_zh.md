# Reachy Mini 平台智能体接入三步流程

本文按当前已确认的平台接口整理，用于后续 `reachy_mini_conversation_app` 开发、联调和部署。

## 0. 当前确认的地址

平台主机：

```text
192.168.2.236
```

已确认端口：

```text
8088：设备激活 HTTP、终端 WebSocket
8085：设备遥测 WebSocket
```

当前 Reachy Mini 设备唯一标识：

```text
reachy-mini-001
```

终端 WebSocket 地址：

```text
ws://192.168.2.236:8088/websocket/terminal/reachy-mini-001
```

## 1. 获取激活码并录入机器设备

设备侧拿到激活码后，调用设备激活接口，把 Reachy Mini 的唯一设备号录入平台。

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

已验证成功返回：

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

- 激活接口使用 `8088`。
- `deviceId` 使用稳定唯一标识 `reachy-mini-001`。
- 返回的 `serverId` 可以保存，但终端 WebSocket 路径当前使用 `reachy-mini-001`。

## 2. 设备绑定数字员工

设备激活后，需要在平台侧把设备绑定到对应数字员工。

昨天联调时确认过：

- 用 `MAC-001` 建立终端 WS，连接后会提示设备未绑定数字员工。
- 用激活返回的 `2056652440241684481` 建立终端 WS，也会提示设备未绑定数字员工。
- 用 `reachy-mini-001` 建立终端 WS，在绑定完成后可以正常接入并返回流式内容。

因此当前规则是：

```text
平台设备唯一标识 = reachy-mini-001
终端 WS 路径标识 = reachy-mini-001
```

数字员工绑定动作在平台后台完成。App 侧只需要保证后续使用同一个设备号发起连接。

## 3. 设备号发起 WebSocket 连接

绑定完成后，设备用自己的唯一设备号拼接终端 WebSocket 地址：

```text
ws://192.168.2.236:8088/websocket/terminal/{deviceId}
```

当前实际地址：

```text
ws://192.168.2.236:8088/websocket/terminal/reachy-mini-001
```

请求头：

```http
authorization: Bearer <login-token>
```

建立连接后，发送简化后的 `ASR_SIGNAL`：

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

字段说明：

| 字段 | 说明 |
| --- | --- |
| `type` | 固定为 `ASR_SIGNAL` |
| `data.user_id` | 当前用户 ID，由 app 配置 |
| `data.session_id` | 会话 ID。相同 `session_id` 复用上下文；更换 `session_id` 即新会话 |
| `data.query` | 对话正文，由 ASR 结果或文本输入产生 |

不再放入消息体的字段：

```text
messageId
timestamp
response_mode
message_id
demp_id
device_id
```

设备身份由 WebSocket 路径中的 `{deviceId}` 表达；数字员工关系由平台绑定关系决定；上下文只由 `session_id` 是否一致决定。

## 4. 流式返回处理

平台会通过同一个 WebSocket 流式返回内容。

昨天已观察到的返回类型：

- `TTS_MESSAGE`：平台返回给设备的消息。
- `data.type == "0"`：正文分片，文本通常在 `data.text` 或 `data.content`。
- `data.type == "2"`：状态消息，例如 `开始推理`、`推理结束`。
- `data.is_end == true` 且 `data.text == "推理结束"`：当前轮对话结束。

App 侧处理建议：

```text
1. 建立 ws://.../websocket/terminal/{deviceId}
2. 发送简化 ASR_SIGNAL
3. 持续接收 TTS_MESSAGE
4. 拼接 data.type == "0" 的正文
5. 收到 推理结束 后结束本轮
6. 把拼接文本交给本地 TTS 播放
```

## 5. Reachy Mini App 中的配置项

建议保留这些可配置项：

```env
REACHY_PLATFORM_TOKEN=<login-token>
REACHY_PLATFORM_DEVICE_ID=reachy-mini-001
REACHY_PLATFORM_USER_ID=1991
REACHY_PLATFORM_SESSION_ID=session-20260519-001
REACHY_PLATFORM_TERMINAL_WS_URL=ws://192.168.2.236:8088/websocket/terminal/reachy-mini-001
REACHY_PLATFORM_HTTP_BASE_URL=http://192.168.2.236:8088
REACHY_PLATFORM_TELEMETRY_WS_URL=ws://192.168.2.236:8085/ws/device/telemetry
```

`session_id` 策略：

- 保持不变：平台保留上下文，适合连续对话。
- 每次新建：平台重新开始上下文，适合一次性任务或新用户。

## 6. 当前项目实现位置

平台客户端：

```text
src/reachy_mini_conversation_app/platform_client.py
```

平台智能体后端：

```text
src/reachy_mini_conversation_app/platform_agent.py
```

WebSocket 测试脚本：

```text
ws_device_telemetry_test/terminal_chat_probe.py
```

当前 app 的对话链路：

```text
Reachy Mini 麦克风
-> 本地 ASR
-> 平台 WebSocket ASR_SIGNAL
-> 平台智能体流式返回
-> 本地 TTS
-> Reachy Mini 播放
```
