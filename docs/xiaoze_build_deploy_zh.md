# Reachy Mini Conversation App 构建与部署流程

本文按 Reachy Mini 官方 SDK 与 Hugging Face 发布流程整理，面向当前项目 `xiaoze_conversation_app`。

参考：

- Build: https://github.com/pollen-robotics/reachy_mini/tree/main/docs/SDK
- Deploy: https://huggingface.co/blog/pollen-robotics/make-and-publish-your-reachy-mini-apps

## 1. 当前项目定位

这是一个 Reachy Mini Python app，适合运行在机器人侧或机器人同局域网的开发机上。

当前项目已经具备官方要求的 Python app 结构：

- `XiaozeConversationApp` 继承 `ReachyMiniApp`
- `run(self, reachy_mini, stop_event)` 作为 dashboard 启停入口
- `pyproject.toml` 声明 `xiaoze_apps` entry point
- `src/xiaoze_conversation_app/static/` 提供配置 UI
- README 根部带 Hugging Face Space metadata

## 2. 本地构建

推荐开发环境：

```bash
uv sync --group dev
```

如果不用 uv：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

## 3. 本地运行

开发机直接跑：

```bash
xiaoze-conversation-app --gradio
```

Dashboard 方式测试：

```bash
pip install -e .
reachy-mini-daemon
```

然后浏览器打开：

```text
http://127.0.0.1:8000/
```

在 installed applications 里找到 `xiaoze_conversation_app`，点击运行。运行后点击配置按钮进入 app UI。

## 4. 配置平台智能体

在配置 UI 中选择 `Platform Agent`，填写：

- `OPENAI_COMPATIBLE_API_KEY`：国内 ASR/TTS 供应商 key
- `Platform token`
- `device_id`：例如 `reachy-mini-001`
- `user_id`：例如 `1991`
- `session_id`：会话 ID，不变则保留上下文
- `Terminal WebSocket`：例如 `ws://192.168.2.236:8088/websocket/terminal/reachy-mini-001`
- `Telemetry WebSocket`：例如 `ws://192.168.2.236:8085/ws/device/telemetry`
- `HTTP base URL`：例如 `http://192.168.2.236:8088`

这些配置会写入本机实例 `.env`。token 不会回显，避免泄露。

## 5. 发布前检查

先跑项目检查：

```bash
uv run ruff check src tests
uv run pytest -q
uv run python -m compileall src
node --check src/xiaoze_conversation_app/static/main.js
```

再跑官方 app 检查：

```bash
reachy-mini-app-assistant check
```

Windows PowerShell 建议打开 UTF-8 模式再跑，否则官方检查工具在读取 UTF-8 README 时可能被系统默认 GBK 编码卡住：

```powershell
$env:PYTHONUTF8="1"
uv run reachy-mini-app-assistant check .
```

这个检查会创建临时虚拟环境并完整安装 app，因此首次运行可能比较慢。

如果命令不存在，先确认当前环境安装了 `reachy-mini`：

```bash
pip install --upgrade reachy-mini
```

## 6. 登录 Hugging Face

安装或升级 CLI：

```bash
pip install --upgrade huggingface_hub
```

使用有写权限的 token 登录：

```bash
hf auth login --token $HF_TOKEN --add-to-git-credential
```

Windows PowerShell：

```powershell
hf auth login --token $env:HF_TOKEN --add-to-git-credential
```

不要把 Hugging Face token、平台 token、模型供应商 key 写进 git。

## 7. 发布

在项目根目录执行：

```bash
reachy-mini-app-assistant publish
```

按提示选择当前 app 路径，并决定 Space 是 public 还是 private。

发布完成后，Hugging Face 会生成类似：

```text
https://huggingface.co/spaces/<username>/xiaoze_conversation_app
```

Dashboard 可以从 Hugging Face 安装该 app。安装后在目标机器上通过配置 UI 填平台 token、ASR/TTS key 和 websocket 地址。

## 8. 部署注意事项

- Reachy Mini Wireless 的算力有限，建议复杂 ASR/TTS、视觉模型放在外部服务或开发机上。
- `--local-vision` 不建议直接跑在 Wireless 的 CM4 上。
- 平台终端 WS 当前使用 `8088`，遥测 WS 当前使用 `8085`。
- `session_id` 不变时平台侧会保留上下文；要新会话时换一个新的 session。
- `device_id` 只用于设备激活和 websocket 路径，例如 `/websocket/terminal/reachy-mini-001`；当前 `ASR_SIGNAL` 消息体不再传 `device_id`。

## 9. 推荐发布节奏

1. 本地 UI 配置通过。
2. 终端 WS 能收到平台智能体流式返回。
3. Dashboard 本地启动与停止正常。
4. `reachy-mini-app-assistant check` 通过。
5. 发布 private Space 给内部测试。
6. 确认稳定后再改为 public。
