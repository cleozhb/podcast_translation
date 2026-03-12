"""配置管理：从 .env 和 config.yaml 加载配置。"""

from pathlib import Path
from typing import Optional

import yaml
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # RSS
    rss_feeds: list[str] = []
    max_episodes_per_feed: int = 5

    # ASR（DashScope 语音识别）
    # 推荐模型：Fun-ASR（最新一代，噪声鲁棒性更强）或 qwen3-asr-flash（性价比高）
    asr_provider: str = "dashscope"
    dashscope_asr_model: str = "fun-asr"

    # LLM 翻译（通义千问）
    # 推荐模型：
    # - qwen-plus：旗舰均衡款，性价比高（推荐）
    # - qwen-max：最强效果，适合复杂场景
    # - qwen-flash：轻量快速，成本低
    llm_provider: str = "qwen"
    dashscope_api_key: str = ""
    llm_model: str = "qwen-plus"

    # TTS（阿里云 CosyVoice）
    # 推荐模型：
    # - cosyvoice-v3-plus：效果最佳，支持声音复刻（推荐）
    # - cosyvoice-v3-flash：速度快，性价比高
    # - cosyvoice-v2：成熟稳定，70+ 音色
    tts_provider: str = "aliyun"
    aliyun_tts_appkey: str = ""
    dashscope_tts_model: str = "cosyvoice-v3-plus"

    # OSS（用于声音克隆时上传声纹样本）
    oss_endpoint: str = ""
    oss_bucket: str = ""
    aliyun_access_key_id: str = ""
    aliyun_access_key_secret: str = ""

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
