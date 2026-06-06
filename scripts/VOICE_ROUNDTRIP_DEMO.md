# Voice Roundtrip Demo

这个测试包用于体验一次完整链路：

```text
麦克风语音输入
-> 192.168.0.234:3100 ASR/VAD
-> 192.168.0.234:3000 小模型判断是否在跟智能体对话
-> 阿里云百炼 qwen-plus 生成回复
-> 192.168.0.234:3200 TTS
-> 本机扬声器播放回复
```

## 1. 列出录音设备

```powershell
.\scripts\run_voice_roundtrip_demo.ps1 -ListDevices
```

记下你想用的输入设备编号。如果不传，使用系统默认输入设备。

## 2. 运行完整交互测试

```powershell
.\scripts\run_voice_roundtrip_demo.ps1
```

如果环境变量里没有 `DASHSCOPE_API_KEY` 或 `BAILIAN_API_KEY`，脚本会提示你输入百炼 Key。

启动后：

1. 按 Enter 开始录音。
2. 对着麦克风说话。
3. 说完后再按 Enter 停止录音。
4. 等待 ASR、过滤、百炼回复、TTS。
5. 听本机扬声器播放回复。

## 网页 UI

启动本地网页：

```powershell
python .\scripts\voice_roundtrip_ui.py
```

然后打开：

```text
http://127.0.0.1:8765
```

页面里填百炼 Key，点击 `Start` 开始录音，点击 `Stop` 停止录音并触发完整链路。页面会显示 ASR、Filter、LLM、TTS 的耗时，并自动播放回复音频。

## 指定麦克风

```powershell
.\scripts\run_voice_roundtrip_demo.ps1 -InputDevice 0
```

## 固定录音 5 秒

```powershell
.\scripts\run_voice_roundtrip_demo.ps1 -FixedRecord -Duration 5
```

## 直接用 Python

```powershell
$env:DASHSCOPE_API_KEY = "你的百炼Key"
python .\scripts\voice_roundtrip_demo.py --host 192.168.0.234
```
