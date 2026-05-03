"""
core/app_factory.py
===================
Shared configuration and provider factory helpers.
"""

import os

import yaml


def load_config(path: str = "config.yaml", quiet: bool = False) -> dict:
    if not os.path.exists(path):
        if not quiet:
            print(f"  ⚠️  配置文件 {path} 不存在，使用默认配置")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def create_providers(config: dict):
    """根据配置创建 Provider 实例"""
    active = config.get("active_providers", {})

    stt_name = active.get("stt", "dashscope")
    if stt_name == "dashscope":
        from providers.dashscope_stt import DashScopeSTT
        stt = DashScopeSTT(config)
    elif stt_name == "baidu":
        from providers.baidu_stt import BaiduSTT
        stt = BaiduSTT(config)
    else:
        raise ValueError(f"未知 STT provider: {stt_name}")

    llm_name = active.get("llm", "dashscope")
    if llm_name == "dashscope":
        from providers.dashscope_llm import DashScopeLLM
        llm = DashScopeLLM(config)
    elif llm_name == "baidu":
        from providers.baidu_llm import BaiduLLM
        llm = BaiduLLM(config)
    else:
        raise ValueError(f"未知 LLM provider: {llm_name}")

    tts_name = active.get("tts", "cosyvoice")
    if tts_name == "cosyvoice":
        from providers.cosyvoice_tts import CosyVoiceTTS
        tts = CosyVoiceTTS(config)
    else:
        raise ValueError(f"未知 TTS provider: {tts_name}")

    storage = None
    if config.get("oss", {}).get("access_key_id"):
        from providers.oss_storage import OSSStorage
        storage = OSSStorage(config)

    return stt, llm, tts, storage


def create_shownote_llm(config: dict):
    from providers.baidu_llm import BaiduLLM

    return BaiduLLM(config)
