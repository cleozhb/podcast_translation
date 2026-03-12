# 英文播客 → 中文自动翻译工作流

## Context

用户希望构建一个自动化工具：通过 RSS 源获取英文播客音频，经过 ASR 转录、LLM 翻译、**声音克隆 TTS 合成**，最终输出用原播客主播音色说中文的语音版播客。偏好使用国内云服务（阿里云/百度），项目目前为空。

## 核心流程

```
RSS Feed → 解析 episode → 下载音频 → ASR 英文转录 → LLM 英→中翻译
                                         ↓
                              自动提取说话人声纹样本
                                         ↓
                              CosyVoice 声音克隆 TTS → 用原播客主播音色合成中文 → 输出 MP3
```

## RSS 获取播客音频原理

播客 RSS 是标准 XML，每个 `<item>` 中的 **`<enclosure>`** 标签包含音频直链：
```xml
<enclosure url="https://media.example.com/ep400.mp3" length="125829120" type="audio/mpeg"/>
```
使用 `feedparser` 库解析后，通过 `entry.enclosures[0].href` 获取音频 URL，直接用 `requests` 流式下载即可。

## 环境管理

使用 **`uv`** 创建和管理虚拟环境，所有依赖安装和脚本执行都在虚拟环境中进行：
```bash
uv venv                    # 创建 .venv 虚拟环境
source .venv/bin/activate  # 激活（后续所有命令都在虚拟环境中）
uv pip install -r requirements.txt  # 安装依赖
```

## 技术选型

| 环节 | 推荐方案 | 备选 |
|------|---------|------|
| RSS 解析 | `feedparser` | - |
| 音频处理 | `pydub` + `ffmpeg` | - |
| ASR | 阿里云录音文件识别（支持6h长音频，异步模式） | 百度长语音识别 |
| 翻译 | 通义千问 `qwen-plus`（via `dashscope` SDK，32K 上下文） | 文心一言 `ernie-4.0` |
| TTS + 声音克隆 | 阿里云 CosyVoice 音色克隆（zero-shot，传入参考音频即可复刻声音） | 百度语音合成 |
| 声纹提取 | 从播客音频中自动截取清晰片段作为参考音频 | 手动指定 |

## 项目结构

```
podcast_translation/
├── main.py                  # CLI 入口
├── config.py                # pydantic-settings 配置管理
├── requirements.txt
├── config.yaml              # RSS 源列表等用户配置
├── .env                     # API 密钥（不入库）
├── rss/
│   ├── feed_parser.py       # RSS 解析，提取 episode + 音频 URL
│   └── audio_downloader.py  # 流式下载 + 格式转换（→WAV 16kHz）
├── asr/
│   ├── base.py              # ASR 抽象基类
│   ├── aliyun_asr.py        # 阿里云录音文件识别（异步：提交→轮询→获取结果）
│   └── audio_splitter.py    # 长音频切片（备用，阿里云异步模式一般不需要）
├── translator/
│   ├── base.py              # 翻译器抽象基类
│   ├── qwen_translator.py   # 通义千问翻译（含分段策略 + 上下文传递）
│   └── prompt_templates.py  # 翻译/摘要 Prompt 模板
├── tts/
│   ├── base.py              # TTS 抽象基类
│   ├── voice_cloner.py      # 声音克隆：从播客音频自动提取声纹样本
│   ├── aliyun_tts.py        # 阿里云 CosyVoice 音色克隆 TTS
│   └── audio_merger.py      # 段落音频拼接
├── pipeline/
│   ├── orchestrator.py      # 流水线总控（RSS→ASR→翻译→TTS）
│   └── progress_tracker.py  # SQLite 进度追踪，支持断点续跑
├── utils/
│   ├── retry.py             # 指数退避重试装饰器
│   └── logger.py            # loguru 日志配置
├── data/                    # 运行时数据（不入库）
    ├── audio_raw/           # 下载的原始音频
    ├── voice_samples/       # 提取的声纹参考音频（按播客名缓存）
    ├── transcripts/         # 英文转录
    ├── translations/        # 中文翻译
    └── audio_output/        # 最终中文语音
```

## 分阶段实现计划

### Phase 1: 基础框架 + RSS 模块
1. 搭建项目结构，创建所有目录和 `__init__.py`
2. `config.py` — 用 `pydantic-settings` 管理配置，从 `.env` 读取 API 密钥
3. `utils/logger.py` + `utils/retry.py` — 基础工具
4. `rss/feed_parser.py` — feedparser 解析 RSS，提取 episode 列表和音频 enclosure URL
5. `rss/audio_downloader.py` — requests 流式下载音频 + pydub 转换格式
6. `requirements.txt` + `.gitignore` + `.env` 模板

### Phase 2: ASR 语音识别
7. `asr/base.py` — ASR 抽象基类
8. `asr/aliyun_asr.py` — 阿里云录音文件识别：获取 token → 提交任务 → 轮询结果
9. `asr/audio_splitter.py` — pydub 长音频切片（备用）

### Phase 3: LLM 翻译
10. `translator/prompt_templates.py` — system prompt + 翻译/摘要模板
11. `translator/base.py` — 翻译器抽象基类
12. `translator/qwen_translator.py` — 通义千问翻译：生成摘要 → 按句子边界分段 → 逐段翻译（传入前文 context）→ 拼接

### Phase 4: TTS 声音克隆 + 语音合成
13. `tts/voice_cloner.py` — 声纹提取：从播客原始音频中自动截取一段清晰的说话片段（10-30秒），作为 CosyVoice 音色克隆的参考音频。策略：利用 ASR 返回的时间戳信息，选取一段连续、清晰、无背景音乐的说话片段；用 pydub 截取并导出为 WAV
14. `tts/base.py` — TTS 抽象基类
15. `tts/aliyun_tts.py` — 阿里云 CosyVoice 音色克隆 TTS：将参考音频 + 翻译文本传入 CosyVoice API，生成克隆音色的中文语音。CosyVoice 支持 zero-shot 音色克隆，只需一段参考音频即可
16. `tts/audio_merger.py` — pydub 拼接段落音频，插入自然停顿

### Phase 5: 流水线整合
16. `pipeline/progress_tracker.py` — SQLite 追踪 episode 处理状态，支持断点续跑
17. `pipeline/orchestrator.py` — 编排完整流程：解析→下载→ASR→翻译→TTS
18. `main.py` — CLI 入口，支持 `python main.py --feed <rss_url>` 等命令

## 关键设计决策

- **长文本翻译策略**：按句子边界分段（每段约 2500 词），翻译时传入前文最后 3 句 + 全文摘要作为 context，保持术语和风格一致
- **声音克隆策略**：从播客音频中自动提取 10-30 秒的清晰说话片段作为参考音频。利用 ASR 返回的带时间戳结果，选取连续、无背景音乐、单人说话的片段。每个播客提取一次，后续同播客 episode 复用同一声纹。CosyVoice 支持 zero-shot 克隆，不需要训练
- **ASR 音频来源**：阿里云异步 ASR 需要一个公网 URL 才能下载音频来识别（它不能读本地文件）。**首选方案**：直接把 RSS 中播客的原始音频 URL 传给 ASR（播客音频本身就托管在 CDN 上，有公网链接，大多数情况 MP3 格式直接兼容）。**保底方案**：如果原始 URL 有防盗链或格式不兼容，则先下载音频到本地，再上传到阿里云 OSS 获取临时 URL
- **进度追踪**：SQLite 记录每个 episode 的 guid 和处理状态，重启后自动跳过已完成的
- **错误处理**：所有 API 调用使用指数退避重试，单 episode 失败不影响其他 episode

## 依赖清单

```
feedparser>=6.0.10      # RSS 解析
pydub>=0.25.1           # 音频处理（需系统安装 ffmpeg）
requests>=2.31.0        # HTTP 下载
dashscope>=1.14.0       # 通义千问 LLM
alibabacloud-nls>=1.0.0 # 阿里云语音 ASR+TTS
oss2>=2.18.0            # OSS 存储
pydantic-settings>=2.0.0
pyyaml>=6.0
python-dotenv>=1.0.0
tqdm>=4.66.0            # 进度条
loguru>=0.7.0           # 日志
```

系统依赖：`ffmpeg`（`apt install ffmpeg` 或 `brew install ffmpeg`）

## 验证方式

以 **Lex Fridman Podcast** 和 **Lenny's Podcast** 作为测试源：

- Lex Fridman: `https://lexfridman.com/feed/podcast/`
- Lenny's Podcast: `https://www.lennysnewsletter.com/feed`

1. **Phase 1 验证**：分别对两个 RSS 源运行 `--list` 解析出 episode 列表并成功下载音频
2. **Phase 2 验证**：对一段 Lex Fridman 的音频执行 ASR，检查英文转录质量
3. **Phase 3 验证**：对转录文本执行翻译，检查中文翻译质量和分段连贯性
4. **Phase 4 验证**：自动提取 Lex Fridman 的声纹样本，执行声音克隆 TTS，对比原声与合成音色
5. **端到端验证**：`python main.py --feed "https://lexfridman.com/feed/podcast/" --episodes 1` 完整跑一期
