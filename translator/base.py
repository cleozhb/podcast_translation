"""翻译器抽象基类。"""

from abc import ABC, abstractmethod


class BaseTranslator(ABC):
    """LLM 翻译抽象基类。"""

    @abstractmethod
    def translate(
        self,
        text: str,
        podcast_name: str = "",
        episode_title: str = "",
    ) -> str:
        """
        将英文文本翻译为中文。

        Args:
            text: 待翻译的英文文本（可能很长，实现类需处理分段）
            podcast_name: 播客名称（用于 context）
            episode_title: episode 标题（用于 context）

        Returns:
            翻译后的中文文本
        """
        ...
