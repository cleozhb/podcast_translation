"""通义千问翻译器：使用 DashScope SDK 调用 qwen 模型进行翻译。"""

import re

from dashscope import Generation
from loguru import logger

from translator.base import BaseTranslator
from translator.prompt_templates import (
    CONTEXT_SECTION,
    SUMMARY_PROMPT,
    SYSTEM_PROMPT,
    TRANSLATE_PROMPT,
)


class QwenTranslator(BaseTranslator):
    """
    通义千问翻译器。

    长文本翻译策略：
    1. 先用 LLM 生成全文摘要，作为全局背景
    2. 按句子边界将文本分段（每段约 2500 词）
    3. 逐段翻译，传入前一段的翻译尾部作为 context
    4. 拼接所有翻译段落
    """

    def __init__(self, api_key: str, model: str = "qwen-plus"):
        self.api_key = api_key
        self.model = model

    def translate(
        self,
        text: str,
        podcast_name: str = "",
        episode_title: str = "",
        chunk_words: int = 2500,
    ) -> str:
        word_count = len(text.split())
        logger.info(f"开始翻译: {word_count} 个英文单词, 模型: {self.model}")

        # 生成全文摘要
        summary = self._generate_summary(text[:5000])
        logger.info(f"摘要生成完成: {summary[:50]}...")

        # 按句子边界分段
        chunks = self._split_by_sentences(text, chunk_words)
        logger.info(f"文本分为 {len(chunks)} 段")

        # 逐段翻译
        translations = []
        previous_tail = ""

        for i, chunk in enumerate(chunks):
            logger.info(f"翻译第 {i + 1}/{len(chunks)} 段...")

            context = CONTEXT_SECTION.format(
                podcast_name=podcast_name,
                episode_title=episode_title,
                summary=summary,
                previous_tail=previous_tail,
            )
            prompt = TRANSLATE_PROMPT.format(context_section=context, text=chunk)

            translation = self._call_llm(prompt)
            translations.append(translation)

            # 取最后 3 句作为下一段的 context
            sentences = translation.split("。")
            previous_tail = (
                "。".join(sentences[-3:]) if len(sentences) >= 3 else translation
            )

        result = "\n\n".join(translations)
        logger.info(f"翻译完成: {len(result)} 个中文字符")
        return result

    def _generate_summary(self, text_preview: str) -> str:
        """用 LLM 生成全文摘要。"""
        prompt = SUMMARY_PROMPT.format(text_preview=text_preview)
        return self._call_llm(prompt)

    def _split_by_sentences(self, text: str, target_words: int) -> list[str]:
        """按句子边界分段，每段不超过 target_words 个词。"""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks = []
        current_chunk: list[str] = []
        current_count = 0

        for sent in sentences:
            word_count = len(sent.split())
            if current_count + word_count > target_words and current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = [sent]
                current_count = word_count
            else:
                current_chunk.append(sent)
                current_count += word_count

        if current_chunk:
            chunks.append(" ".join(current_chunk))
        return chunks

    def _call_llm(self, user_prompt: str) -> str:
        """调用通义千问 API。"""
        response = Generation.call(
            api_key=self.api_key,
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            result_format="message",
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"LLM 调用失败: {response.status_code} - {response.message}"
            )
        return response.output.choices[0].message.content
