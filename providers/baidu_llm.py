"""
providers/baidu_llm.py
======================
百度千帆 文心一言 翻译
文档: https://cloud.baidu.com/doc/WENXINWORKSHOP/index.html
"""

import time
import requests
from providers.base import LLMProvider, TranslationResult


# 模型名称到 API 路径的映射
MODEL_ENDPOINTS = {
    "ernie-4.0-8k": "completions_pro",
    "ernie-3.5-8k": "completions",
    "ernie-speed-8k": "ernie_speed",
    "ernie-lite-8k": "ernie-lite-8k",
}


class BaiduLLM(LLMProvider):
    """百度千帆 文心一言"""

    TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
    BASE_URL = "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat"

    def __init__(self, config: dict):
        super().__init__(config)
        bc = config.get("baidu", {})
        self.api_key = bc.get("api_key", "")
        self.secret_key = bc.get("secret_key", "")
        self.model = bc.get("llm_model", "ernie-4.0-8k")
        self._token = None
        self._token_expires = 0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires:
            return self._token

        resp = requests.post(self.TOKEN_URL, params={
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key,
        })
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = time.time() + data.get("expires_in", 2592000) - 60
        return self._token

    def translate(self, text: str, system_prompt: str = "") -> TranslationResult:
        print(f"  🌐 [百度 LLM] 翻译中... (模型: {self.model})")

        token = self._get_token()
        endpoint = MODEL_ENDPOINTS.get(self.model, "completions_pro")
        url = f"{self.BASE_URL}/{endpoint}?access_token={token}"

        body = {
            "messages": [{"role": "user", "content": text}],
        }
        if system_prompt:
            body["system"] = system_prompt

        resp = requests.post(url, json=body)
        data = resp.json()

        if "result" in data:
            translated = data["result"]
            print(f"  ✅ 翻译完成，译文 {len(translated)} 字")
            return TranslationResult(
                source_text=text,
                translated_text=translated,
            )
        else:
            raise RuntimeError(f"百度 LLM 失败: {data.get('error_msg', data)}")