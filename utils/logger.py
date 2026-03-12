"""日志配置。"""

import sys
from loguru import logger


def setup_logger(level: str = "INFO"):
    """配置 loguru 日志格式。"""
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        level=level,
    )
    logger.add(
        "data/podcast_translation.log",
        rotation="10 MB",
        retention="7 days",
        level="DEBUG",
    )
    return logger
