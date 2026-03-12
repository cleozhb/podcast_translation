"""ASR 抽象基类。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TranscriptSegment:
    """转录片段，包含时间戳信息。"""

    text: str
    start_ms: int  # 开始时间（毫秒）
    end_ms: int  # 结束时间（毫秒）


@dataclass
class TranscriptResult:
    """ASR 转录结果。"""

    full_text: str
    segments: list[TranscriptSegment]
    language: str = "en"


class BaseASR(ABC):
    """ASR 语音识别抽象基类。"""

    @abstractmethod
    def transcribe(self, audio_url: str) -> TranscriptResult:
        """
        对音频进行语音识别。

        Args:
            audio_url: 音频的公网 URL（云端 ASR 需要能下载到音频）

        Returns:
            TranscriptResult: 转录结果，包含完整文本和带时间戳的片段
        """
        ...
