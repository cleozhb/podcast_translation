# CosyVoice TTS 声音克隆使用指南

## ⚠️ 重要提示

**cosyvoice-v1 不支持声音克隆功能!** 

如果您需要使用声音克隆 (复刻特定人的声音),必须:
1. 升级模型到 `cosyvoice-v2` 或 `cosyvoice-v3-plus`
2. 使用官方的声音复刻 API 创建音色获取 `voice_id`
3. **不能直接将 OSS URL 作为 `voice` 参数使用**

## 📋 目录

- [快速开始 (使用默认音色)](#快速开始使用默认音色)
- [声音克隆完整流程](#声音克隆完整流程)
- [常见问题解决](#常见问题解决)

---

## 🚀 快速开始 (使用默认音色)

### 1. 保持配置不变

```yaml
# config.yaml
cosyvoice:
  api_key: "sk-xxx"
  model: "cosyvoice-v1"  # 或其他支持的模型
  voiceprint_duration: 20
```

### 2. 跳过声纹提取步骤

```bash
python main.py --skip-voiceprint
```

系统会自动使用默认音色 `longxiaochun` 进行合成。

---

## 🎨 声音克隆完整流程

### 前提条件

- ✅ 模型已升级到 `cosyvoice-v2` 或 `cosyvoice-v3-plus`
- ✅ 准备了 10-30 秒清晰朗读音频 (无背景音乐)
- ✅ 已将音频上传到 OSS 或其他可公网访问的位置

### Step 1: 准备声纹音频

**音频要求:**
- 格式：WAV/MP3/M4A
- 时长：10-30 秒
- 大小：≤10MB
- 采样率：≥16kHz
- 内容：清晰、连续的朗读，无背景音乐

**示例:**
```bash
# 从播客音频中提取声纹片段
ffmpeg -i input.mp3 -ss 60 -t 20 -vn -acodec libmp3lame -ar 44100 -ab 192k voiceprint.mp3
```

### Step 2: 上传到 OSS (可选)

如果已有公网可访问的音频 URL，可跳过此步。

使用项目配置的 OSS:
```bash
# 手动上传或使用 OSS 工具
ossutil cp voiceprint.mp3 oss://cleo-oss-bucket/podcast-voiceprints/voiceprint.mp3
```

获得 URL 类似:
```
https://cleo-oss-bucket.oss-cn-wulanchabu.aliyuncs.com/podcast-voiceprints/voiceprint.mp3
```

### Step 3: 创建音色

运行声音克隆工具:

```bash
python create_voice_clone.py
```

**交互式操作流程:**

1. 脚本会检查当前模型版本
2. 如果不是 v2/v3-plus，会提示升级
3. 输入声纹音频的公网 URL
4. 自动提交创建请求并等待就绪
5. 返回 `voice_id` (类似 `cosyvoice-myvoice-abc123`)

**示例输出:**
```
============================================================
🎨 创建声音克隆音色
============================================================
音频 URL: https://cleo-oss-bucket.../voiceprint.mp3
音色前缀：myvoice
目标模型：cosyvoice-v2

Step 1: 提交音色创建请求...
✅ 音色创建成功!
   Voice ID: cosyvoice-myvoice-x7k2m9p
   Request ID: req-123456789

Step 2: 等待音色就绪...
   [1/30] 状态：PROCESSING
   [2/30] 状态：PROCESSING
   [3/30] 状态：OK

✅ 音色已就绪！

============================================================
✅ 声音克隆创建完成!
============================================================
Voice ID: cosyvoice-myvoice-x7k2m9p
```

### Step 4: 更新配置文件

**方式 1: 修改 config.yaml**

```yaml
cosyvoice:
  api_key: "sk-xxx"
  model: "cosyvoice-v2"  # ⚠️ 必须与创建音色时的模型一致
  # 添加 voice_id 配置
  voice_id: "cosyvoice-myvoice-x7k2m9p"
```

**方式 2: 在代码中使用**

```python
from dashscope.audio.tts_v2 import SpeechSynthesizer

synthesizer = SpeechSynthesizer(
    model="cosyvoice-v2",
    voice="cosyvoice-myvoice-x7k2m9p"  # 使用创建的音色ID
)
audio_data = synthesizer.call("要合成的文本")
```

### Step 5: 测试音色

```bash
# 测试刚刚创建的音色
python create_voice_clone.py
# 选择测试选项，输入 voice_id
```

或直接运行主程序:
```bash
python main.py
```

---

## ❌ 常见问题解决

### 错误 418: InvalidParameter

**原因:**
- ❌ 直接将 OSS URL 作为 `voice` 参数使用
- ❌ 模型版本与音色版本不匹配 (v1 模型用 v2 音色)
- ❌ 音色状态不是 "OK"

**解决方案:**
1. 确认使用的是 `voice_id` 而不是 OSS URL
2. 检查模型和音色版本是否匹配
3. 通过 `query_voice()` 查询音色状态

### cosyvoice-v1 能用声音克隆吗？

**不能。** cosyvoice-v1 不支持声音克隆功能。

**替代方案:**
1. 升级到 `cosyvoice-v2` (推荐)
2. 使用默认音色 `longxiaochun`

### 音色创建失败怎么办？

**可能原因:**
- 音频质量不佳 (有背景音、不清晰)
- 音频时长不符合要求 (10-30 秒)
- 音频格式不支持

**解决方案:**
1. 重新录制高质量音频
2. 确保是单人朗读，无背景音乐
3. 调整时长到 10-30 秒

### 如何查看已创建的音色列表？

```python
from dashscope.audio.tts_v2 import VoiceEnrollmentService

service = VoiceEnrollmentService()
voice_list = service.list_voices()
for voice in voice_list:
    print(f"ID: {voice['voice_id']}, Status: {voice['status']}")
```

---

## 💡 最佳实践

### 1. 声纹提取建议

- **时间点选择**: 选择播客中主持人清晰说话的片段
- **避开音乐**: 确保无背景音乐或音效
- **语速适中**: 选择正常语速的片段
- **情感自然**: 避免过于激动或低沉的语气

### 2. 成本控制

- `cosyvoice-v2`: 性价比最高，适合批量使用
- `cosyvoice-v3-plus`: 音质最佳，但成本较高，适合对音质要求高的场景

### 3. 音色管理

- 为不同播客创建不同的音色
- 使用有意义的 `prefix` (如播客名缩写)
- 定期清理不再使用的音色

---

## 📞 获取帮助

- 阿里云官方文档：[CosyVoice 声音复刻 API](https://help.aliyun.com/zh/model-studio/developer-reference/cosyvoice-clone-api)
- DashScope SDK 文档：[Python SDK](https://help.aliyun.com/zh/model-studio/developer-reference/cosyvoice-python-api)

---

## 🔗 相关工具

- [`create_voice_clone.py`](./create_voice_clone.py) - 声音克隆创建工具
- [`test_tts.py`](./test_tts.py) - TTS 功能测试脚本
- [`test_websocket.py`](./test_websocket.py) - WebSocket 连接测试
