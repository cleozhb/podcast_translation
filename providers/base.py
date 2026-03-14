"""
providers/base.py
=================
所有 Provider 的抽象基类。
新增 Provider 只需继承对应基类并实现抽象方法。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# 数据模型
# ============================================================

@dataclass
class TranscriptSegment:
    """转写文本的一个片段"""
    start: float          # 开始时间（秒）
    end: float            # 结束时间（秒）
    text: str             # 文本内容
    speaker: str = ""     # 说话人标识（如果支持）


@dataclass
class TranscriptResult:
    """STT 完整结果"""
    segments: list[TranscriptSegment] = field(default_factory=list)
    full_text: str = ""
    language: str = "en"
    duration: float = 0.0

    def to_plain_text(self) -> str:
        """输出纯文本，按段落分隔"""
        if self.full_text:
            return self.full_text
        return "\n\n".join(seg.text for seg in self.segments)

    def to_timestamped_text(self) -> str:
        """输出带时间戳的文本"""
        lines = []
        for seg in self.segments:
            ts = f"[{_fmt_time(seg.start)} -> {_fmt_time(seg.end)}]"
            lines.append(f"{ts} {seg.text}")
        return "\n".join(lines)


@dataclass
class TranslationResult:
    """翻译结果"""
    source_text: str = ""
    translated_text: str = ""
    # 按段落对齐的翻译（如果 LLM 支持）
    segments: list[dict] = field(default_factory=list)


@dataclass
class TTSResult:
    """TTS 结果"""
    audio_path: str = ""      # 本地音频文件路径
    duration: float = 0.0     # 时长（秒）
    sample_rate: int = 0


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ============================================================
# 抽象基类
# ============================================================

class STTProvider(ABC):
    """语音转文字 Provider"""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def transcribe(self, audio_path: str, language: str = "en") -> TranscriptResult:
        """
        将音频文件转为文字。

        Args:
            audio_path: 本地音频文件路径
            language: 音频语言代码

        Returns:
            TranscriptResult
        """
        ...

    def name(self) -> str:
        return self.__class__.__name__


class LLMProvider(ABC):
    """大语言模型翻译 Provider"""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def translate(self, text: str, system_prompt: str = "") -> TranslationResult:
        """
        将英文文本翻译为中文。

        Args:
            text: 源文本
            system_prompt: 系统提示词

        Returns:
            TranslationResult
        """
        ...

    def translate_chunks(self, chunks: list[str], system_prompt: str = "") -> TranslationResult:
        """
        分块翻译长文本。默认实现：逐块调用 translate 后拼接。
        子类可覆盖此方法实现更高效的批量翻译。
        """
        all_translated = []
        for i, chunk in enumerate(chunks, 1):
            print(f"    翻译第 {i}/{len(chunks)} 段...")
            result = self.translate(chunk, system_prompt)
            all_translated.append(result.translated_text)

        return TranslationResult(
            source_text="\n\n".join(chunks),
            translated_text="\n\n".join(all_translated),
        )

    def name(self) -> str:
        return self.__class__.__name__


class TTSProvider(ABC):
    """文本转语音 Provider"""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def synthesize(
        self,
        text: str,
        output_path: str,
        voice_url: Optional[str] = None,
    ) -> TTSResult:
        """
        将中文文本合成为语音。

        Args:
            text: 要合成的文本
            output_path: 输出音频文件路径
            voice_url: 声纹参考音频的公网 URL（用于声音克隆）

        Returns:
            TTSResult
        """
        ...

    def name(self) -> str:
        return self.__class__.__name__


class StorageProvider(ABC):
    """文件存储 Provider（上传文件获取公网 URL）"""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def upload(self, local_path: str, remote_key: str) -> str:
        """
        上传文件并返回公网可访问 URL。

        Args:
            local_path: 本地文件路径
            remote_key: 远程存储的 key/路径

        Returns:
            公网 URL
        """
        ...

    @abstractmethod
    def delete(self, remote_key: str) -> bool:
        """删除远程文件"""
        ...