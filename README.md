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
uv sync                    # 创建 .venv 虚拟环境
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
podcast-translator/
│
├── pyproject.toml              # 项目元数据 & 依赖（uv 读取此文件）
├── uv.lock                     # uv sync 自动生成的锁文件（提交到 git）
├── .python-version             # Python 版本锁定
├── config.yaml                 # 配置文件（API Key、Provider 选择、OSS 配置等）
├── main.py                     # 入口：交互式选择播客 → 启动工作流
├── requirements.txt            # 兼容 pip 用户
├── .gitignore                  # Git 忽略规则
│
├── core/
│   ├── __init__.py
│   ├── pipeline.py             # 工作流编排引擎
│   ├── podcast_fetcher.py      # RSS 解析 & 音频下载
│   └── audio_utils.py          # 音频预处理（切片、提取声纹片段、格式转换）
│
├── providers/
│   ├── __init__.py
│   ├── base.py                 # 抽象基类：STTProvider, LLMProvider, TTSProvider
│   ├── dashscope_stt.py        # 阿里云 DashScope Paraformer 语音识别
│   ├── dashscope_llm.py        # 阿里云 DashScope 通义千问翻译
│   ├── baidu_stt.py            # 百度千帆 语音识别
│   ├── baidu_llm.py            # 百度千帆 文心一言翻译
│   ├── cosyvoice_tts.py        # 阿里云 CosyVoice 声音克隆 TTS
│   └── oss_storage.py          # 阿里云 OSS 上传（供 TTS 使用）
│
└── output/                     # 输出目录（已加入 .gitignore）
    ├── audio/                  # 下载的原始音频
    ├── transcripts/            # 英文转写文本
    ├── translations/           # 中文翻译文本
    ├── voiceprints/            # 提取的声纹片段
    └── final/                  # 最终中文音频
```

todo:
1. 人工控制步骤：先只做英文转中文，用便宜模型，先看文本大概看看内容是否感兴趣
