"""
providers/dashscope_llm.py
==========================
阿里云 DashScope 通义千问 翻译
"""

import dashscope
from dashscope import Generation
from providers.base import LLMProvider, TranslationResult


class DashScopeLLM(LLMProvider):
    """阿里云 DashScope 通义千问"""

    def __init__(self, config: dict):
        super().__init__(config)
        dc = config.get("dashscope", {})
        dashscope.api_key = dc.get("api_key", "")
        self.model = dc.get("llm_model", "qwen-plus")

    def translate(self, text: str, system_prompt: str = "") -> TranslationResult:
        print(f"  🌐 [DashScope LLM] 翻译中... (模型: {self.model})")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": text})

        response = Generation.call(
            model=self.model,
            messages=messages,
            result_format="message",
        )

        if response.status_code == 200:
            translated = response.output.choices[0].message.content
            print(f"  ✅ 翻译完成，译文 {len(translated)} 字")
            return TranslationResult(
                source_text=text,
                translated_text=translated,
            )
        else:
            raise RuntimeError(
                f"DashScope LLM 失败: {response.status_code} - {response.message}"
            )