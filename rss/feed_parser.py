"""RSS Feed 解析模块：从播客 RSS 源提取 episode 列表和音频 URL。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import feedparser
import requests
from loguru import logger


@dataclass
class PodcastEpisode:
    """播客 episode 数据模型。"""

    guid: str
    title: str
    published_date: datetime
    audio_url: str
    audio_type: str
    audio_length: int  # 字节
    duration_seconds: Optional[int]
    description: str = ""
    podcast_name: str = ""


def parse_itunes_duration(duration_str: str) -> Optional[int]:
    """将 itunes:duration 解析为秒数。支持 "7890"、"45:30"、"1:15:30" 等格式。"""
    if not duration_str:
        return None
    parts = duration_str.strip().split(":")
    try:
        if len(parts) == 1:
            return int(parts[0])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        return None
    return None


def fetch_episodes(feed_url: str) -> list[PodcastEpisode]:
    """
    从 RSS feed URL 解析所有 episode。

    播客 RSS 中的 <enclosure> 标签包含音频文件的直接下载 URL。
    feedparser 将其解析到 entry.enclosures 列表中，通过 enclosure.href 获取。
    """
    logger.info(f"正在解析 RSS: {feed_url}")

    # 使用 requests 下载 feed 内容（处理 SSL 问题），再交给 feedparser 解析
    try:
        resp = requests.get(feed_url, timeout=30, headers={"User-Agent": "PodcastTranslator/1.0"})
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
    except requests.RequestException as e:
        raise ValueError(f"Feed 下载失败: {e}") from e

    if feed.bozo and not feed.entries:
        raise ValueError(f"Feed 解析失败: {feed.bozo_exception}")

    podcast_name = feed.feed.get("title", "Unknown Podcast")
    logger.info(f"播客: {podcast_name}，共 {len(feed.entries)} 个 episode")

    episodes = []
    for entry in feed.entries:
        # 从 enclosures 中提取音频 URL
        audio_url = None
        audio_type = ""
        audio_length = 0

        for enc in entry.get("enclosures", []):
            mime = enc.get("type", "")
            if mime.startswith("audio/"):
                audio_url = enc.get("href", "")
                audio_type = mime
                audio_length = int(enc.get("length", 0) or 0)
                break

        # 备选：从 links 中查找 enclosure 类型的音频链接
        if not audio_url:
            for link in entry.get("links", []):
                if link.get("rel") == "enclosure" and link.get("type", "").startswith(
                    "audio/"
                ):
                    audio_url = link.get("href", "")
                    audio_type = link.get("type", "")
                    audio_length = int(link.get("length", 0) or 0)
                    break

        if not audio_url:
            continue

        # 解析发布时间
        pub_parsed = entry.get("published_parsed")
        if pub_parsed:
            published_date = datetime(*pub_parsed[:6])
        else:
            published_date = datetime.now()

        # 解析时长
        duration_str = entry.get("itunes_duration", "")
        duration_seconds = parse_itunes_duration(duration_str)

        episode = PodcastEpisode(
            guid=entry.get("id", entry.get("link", audio_url)),
            title=entry.get("title", "Untitled"),
            published_date=published_date,
            audio_url=audio_url,
            audio_type=audio_type,
            audio_length=audio_length,
            duration_seconds=duration_seconds,
            description=entry.get("summary", ""),
            podcast_name=podcast_name,
        )
        episodes.append(episode)

    episodes.sort(key=lambda e: e.published_date, reverse=True)
    logger.info(f"解析完成，共 {len(episodes)} 个有效 episode")
    return episodes
