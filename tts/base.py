"""TTS 抽象基类。"""

from abc import ABC, abstractmethod
from pathlib import Path


class BaseTTS(ABC):
    """TTS 语音合成抽象基类。"""

    @abstractmethod
    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice_sample_path: Path | None = None,
    ) -> Path:
        """
        将文本合成为语音。

        Args:
            text: 待合成的中文文本
            output_path: 输出音频文件路径
            voice_sample_path: 声音克隆参考音频路径（可选）

        Returns:
            输出音频文件路径
        """
        ...
