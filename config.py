"""配置管理：从 .env 和 config.yaml 加载配置。"""

from pathlib import Path
from typing import Optional

import yaml
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # RSS
    rss_feeds: list[str] = []
    max_episodes_per_feed: int = 5

    # 阿里云通用
    aliyun_access_key_id: str = ""
    aliyun_access_key_secret: str = ""

    # ASR（阿里云录音文件识别）
    asr_provider: str = "aliyun"
    aliyun_asr_appkey: str = ""

    # LLM 翻译（通义千问）
    llm_provider: str = "qwen"
    dashscope_api_key: str = ""
    llm_model: str = "qwen-plus"

    # TTS（阿里云 CosyVoice）
    tts_provider: str = "aliyun"
    aliyun_tts_appkey: str = ""

    # OSS（用于 ASR 上传音频的保底方案）
    oss_endpoint: str = ""
    oss_bucket: str = ""

    # 路径
    data_dir: str = "data"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def load_settings() -> Settings:
    """加载配置：先从 .env 加载 API 密钥，再从 config.yaml 加载 RSS 源等。"""
    settings = Settings()

    config_path = Path("config.yaml")
    if config_path.exists():
        with open(config_path) as f:
            yaml_config = yaml.safe_load(f) or {}
        if "rss_feeds" in yaml_config:
            settings.rss_feeds = yaml_config["rss_feeds"]
        if "max_episodes_per_feed" in yaml_config:
            settings.max_episodes_per_feed = yaml_config["max_episodes_per_feed"]

    return settings
