# 更新日志

## v0.6.3 - 2026-06-03 15:20 CST

### 实时语音

- 修复阿里云 realtime 回复仍然慢放的问题：基类现在分别使用后端声明的输入采样率和输出采样率，阿里云输入保持 16kHz，输出按 24kHz 播放。
- 预览 TTS 和实时回复现在都按各自真实采样率进入机器人播放队列，避免实时回答被 24kHz 音频误当作 16kHz 播放。

## v0.6.2 - 2026-06-03 15:07 CST

### 配置与语音

- 修复三个场景个性预设的音色保存位置错误：现在编辑“展厅机器人 / 会议机器人 / 秘书机器人”的音色会保存为同名覆盖配置，UI 和 realtime session 都会优先读取覆盖后的音色。
- 个性页“应用”默认同时持久化当前个性和该个性的音色，避免重启后又回到语音页之前保存的旧音色。
- 阿里云 realtime 输出音频格式从 `pcm24` 改为 `pcm16`，与当前机器人播放管线的 16-bit PCM 解码方式一致，修复对话语音被拉长、慢放、音色变形的问题。

## v0.6.1 - 2026-06-03 14:51 CST

### 音色试听

- 修复音色切换后 UI 显示已触发试听、但阿里云 realtime 链路不一定实际出声的问题。
- 阿里云 realtime 下的“你好吗”试听改为调用 DashScope 原生 Qwen3-TTS 合成音频，并推入机器人扬声器播放队列。
- 试听合成优先使用当前配置的 TTS 模型，失败时自动尝试 `qwen3-tts-flash` 和 `qwen3-tts`。

## v0.6.0 - 2026-06-03 14:30 CST

### 设置热应用

- 个性页“应用”现在会直接调用运行中的会话热应用 profile，不再只保存为“重启后生效”。
- 语音页切换音色现在会直接调用运行中的 handler 切换音色，并把生效后的音色写入启动配置。
- realtime 个性/音色切换优先使用 `session.update` 热更新；热更新成功时不再重启 realtime session，避免配置后音频链路掉线。
- 每次通过语音页切换音色，或通过个性预设切换到新音色后，机器人会用新音色单独试听一句“你好吗”。
- 修复 SPA 模式下个性/音色接口拿不到机器人音频会话事件循环的问题，避免 UI 保存了但运行链路没有变化。
- 三个场景预设新增默认音色：`展厅机器人=Ryan`、`会议机器人=Serena`、`秘书机器人=Vivian`。
- 应用个性时会同步读取该 profile 的 `voice.txt`，让 prompt 和音色一起生效。

## v0.5.0 - 2026-06-03 14:18 CST

### 机器人安全

- 保留 xiaoze app 启动和关闭时的正常动作，但改为基于当前姿态的小幅礼貌动作：轻微点头、轻微天线摆动，不再执行回中、睡眠位或大角度复位。
- `MovementManager` 初始化时优先读取机器人当前头部和天线姿态作为基准，避免启动线程把默认 neutral 姿态直接下发给机器人。
- 待机呼吸动作改为围绕当前姿态做 5 mm 内的轻微起伏，天线摆幅从 15 度降到 3 度，不再先插值回 neutral。
- 生命周期动作和呼吸动作都保持 `body_yaw=None`，不主动控制底座/身体旋转。

## v0.4.0 - 2026-06-03 14:05 CST

### 机器人安全

- xiaoze app 连接 Reachy Mini SDK 时显式关闭 `automatic_body_yaw`，避免 app 启动后的头部目标姿态带动底座大角度旋转。
- xiaoze app 关闭时不再执行 `goto_target(... neutral ...)` 复位动作；关闭只停止运动线程和清空队列，不再主动把头和天线拉回中位。
- daemon 默认不再执行 `wake_up_on_start`。
- daemon 默认不再执行 `goto_sleep_on_stop`。
- 无线启动脚本增加 `--no-goto-sleep-on-stop`，避免 systemd stop/restart 时把天线打到睡眠位。
- `/api/daemon/restart` 明确以 `wake_up_on_start=false`、`goto_sleep_on_stop=false` 调用 restart，避免后台重启接口触发危险复位。
- 已按安全顺序部署：先调用 `/api/daemon/stop?goto_sleep=false` 停止旧后端，再重启 systemd 加载新参数。

### 验证

- 树莓派已部署新版 xiaoze app 和 daemon 文件。
- daemon 启动日志确认 `wake_up_on_start=False`，未出现 `Waking up Reachy Mini` 或 `Putting Reachy Mini to sleep`。
- 停止 xiaoze app 时日志确认：`Stopping movement manager without commanding a reset pose...`。
- xiaoze app 已重新启动，`/conversation/status` 返回 `running=true` 且 `error=""`。

## v0.3.0 - 2026-06-03 13:20 CST

### 设置页与个性

- 新增三个内置中文个性预设：`展厅机器人`、`会议机器人`、`秘书机器人`。
- 个性选择器只保留上述三个预设，不再显示默认英文预设、旧目录或用户测试 profile。
- 个性和音色“应用”改为只保存启动配置，并提示重启应用后生效；不再运行中热重启 realtime 音频会话，避免配置后卡在“等待机器人音频链路启动”。
- 修复配置页“新建 / 应用 / 删除”按钮在窄界面溢出的问题。
- `/voices` 和 `/voices/current` 改为直接返回后端元数据，避免 realtime 忙时语音设置页空白。
- `/status` 现在返回当前 handler 实际使用的音色，而不是只返回后端默认音色。
- 修复中文个性名称保存问题：允许 Unicode profile 名称，空名称回退为“自定义个性”。

### 中文对话

- 默认身份从 Reachy Mini 改为“小泽”，同时保留 Reachy Mini 作为硬件平台描述。
- 默认 prompt 改为简体中文优先。
- 共享 prompt loader 追加中文优先规则，让自定义 profile 也默认用中文交流。
- 强制 realtime 文字输入和工具结果回复都请求简洁中文语音回答。
- 为 Hugging Face realtime 和阿里云 realtime 后端补充中文优先 session instructions。
- OpenAI-compatible realtime 转写使用配置的 ASR 语言，默认 `zh`。
- 加强 Hugging Face realtime ASR prompt，降低机器人麦克风中文被误识别为短英文的概率。

## v0.2.0 - 2026-06-03 11:30 CST

### 阿里云 Qwen-Omni Realtime

- 对齐阿里云百炼 Qwen-Omni realtime WebSocket 协议。
- `response.create` 不再发送 OpenAI 风格的 response payload。
- 保留 VAD 的 `create_response` 和 `interrupt_response` 参数。
- 将阿里云 partial ASR 的 `text` / `stash` 事件归一成前端监控可读的转写增量。
- Qwen-Omni realtime 模型暂不发送不支持的 Function Calling tools。
- 文字输入监控区明确提示：阿里云 realtime 使用机器人端麦克风作为语音输入。
- 按文档调整阿里云 realtime 音频配置：输入 `pcm16`，输出 `pcm24`，并显式开启输入转写。
- 将阿里云 realtime 服务端 VAD 阈值从 `0.5` 调低到 `0.2`，恢复 800 ms 静音窗口，让正常中文说话更容易被识别。
- 默认阿里云 realtime 音色从 `Ethan` 改为 `Cherry`，并移除残留的硬编码 `Ethan` 回退逻辑。

## v0.1.0 - 2026-06-02 23:40 CST

### 初始同步

- 从树莓派当前已部署的 `xiaoze_conversation_app` 包同步本地源码。
- 从已部署快照中补回 `/personalities/delete` 接口。
- 增加阿里云 realtime 后端模型选项：`qwen3.5-omni-flash-realtime`、`qwen3-omni-flash-realtime`、`qwen-omni-turbo-realtime`。
- 新增 Free HF realtime 模式，作为免 TTS 权限的备用链路。

### 机器人端对话链路

- SPA 模式启动机器人端本地音频循环，让树莓派麦克风和扬声器负责对话，而不是浏览器麦克风。
- 新增 `/conversation/status`、`/conversation/messages`、`/conversation/text`，用于查看机器人端音频链路、聊天记录和文字输入。
- 主语音页改为机器人对话监控，显示机器人麦克风/扬声器电平和本地 handler 文本输入。
- 后端对话错误会写入监控聊天历史，避免失败时 UI 看起来完全无响应。
- 改进机器人对话监控状态：音频循环停止时返回 `running=false` 并暴露最后错误。

### 设置页

- 重做设置页：拆成推荐的阿里云 realtime 语音模式，以及高级 ASR / LLM / TTS 拆分模式。
- 后端切换时通过 `model_name` 正确持久化 realtime 模型。
- 精简 xiaoze 设置 UI，凭据不再以两个大面板常驻占位。
- 增加紧凑 provider tabs，只显示当前链路需要的凭据字段。
- 麦克风可视化改为读取本地麦克风电平，说话时能驱动音频图。
- 后端配置保存后显示服务端返回信息，并立即刷新凭据状态。
- SPA 状态增加 `has_openai_compatible_key`，便于 UI 判断兼容接口 key 是否缺失。

### 运动安全

- 移除 app stop 后 daemon 级别强制回中动作，避免启动/停止/重启 xiaoze 时额外触发机器人复位。
- xiaoze 运动循环在 idle、呼吸、头部追踪偏移、头部小动作时不再下发 `body_yaw=0`。
- 修复 `move_head` 将天线关节值误当成 body yaw 的问题，避免看左/右/上/下/前时重置身体。
- 关闭清理时保留 `body_yaw` 不受控，避免额外转身。

### 凭据与模型

- 阿里云 TTS 音色选择归一化：无效 OpenAI 音色如 `alloy` 会替换为阿里云兼容默认值。
- 拆分后端 readiness 检查现在能准确报告缺失凭据：
  - `ALIYUN_API_KEY` 缺失时 `has_aliyun_key=false`。
  - ASR / LLM / TTS 选择阿里云但没有 key 时 `can_proceed=false`。
- 拒绝在缺少阿里云 key 时保存依赖阿里云组件的拆分后端配置。

### 部署记录

- 已部署 xiaoze 文件到树莓派：
  `/venvs/apps_venv/lib/python3.13/site-packages/xiaoze_conversation_app`
- 已部署 daemon `manager.py` 修复到：
  `/home/roger/app/reachy_mini/src/reachy_mini/apps/manager.py`
- 已重启 `reachy-mini-daemon.service` 并验证：
  - daemon service active。
  - `xiaoze_conversation_app` 可通过 daemon app manager 启动。
  - `http://127.0.0.1:7860/ready` 返回 ready。
  - `http://127.0.0.1:7860/status` 能返回后端状态。
  - 保存当前阿里云配置时不需要重复输入 key。
  - `http://127.0.0.1:7860/conversation/status` 返回 `running=true`，并能看到机器人麦克风输入电平。
  - `http://127.0.0.1:7860/conversation/text` 能进入本地 handler，并把错误记录到监控历史。

## 已知问题 - 2026-06-03 14:05 CST

- 部分阿里云模型权限取决于当前 API Key 授权范围；如果出现 `Model.AccessDenied`，需要在百炼侧确认该模型是否已开通。
