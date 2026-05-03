import hashlib
import re
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import feedparser
import requests

from core.shownote_generator import extract_shownote_from_entry


USER_AGENT = "Mozilla/5.0 (PodcastTranslator/1.0)"


def search_apple_podcasts(
    query: str,
    limit: int = 10,
    timeout: int = 15,
    proxy: str | None = None,
) -> list[dict]:
    proxies = {"http": proxy, "https": proxy} if proxy else None
    resp = requests.get(
        "https://itunes.apple.com/search",
        params={"term": query, "media": "podcast", "limit": limit, "country": "US"},
        timeout=timeout,
        proxies=proxies,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def find_feeds(query: str, config: dict, limit: int = 10) -> list[dict]:
    rss_cfg = config.get("rss", {})
    timeout = rss_cfg.get("timeout", 15)
    proxy = rss_cfg.get("proxy")
    results = search_apple_podcasts(query, limit=limit, timeout=timeout, proxy=proxy)
    feeds = []
    for item in results:
        rss_url = item.get("feedUrl") or ""
        if not rss_url:
            continue
        feed_info = _probe_feed(rss_url, timeout=timeout, proxy=proxy)
        title = item.get("collectionName") or feed_info.get("title", "")
        publisher = item.get("artistName") or feed_info.get("publisher", "")
        feeds.append({
            "title": title,
            "publisher": publisher,
            "rss_url": rss_url,
            "website_url": item.get("collectionViewUrl", ""),
            "description": feed_info.get("description", ""),
            "language": feed_info.get("language", ""),
            "confidence": _confidence(query, title, publisher, bool(feed_info.get("valid"))),
        })
    feeds.sort(key=lambda f: f["confidence"], reverse=True)
    return feeds


def fetch_feed(rss_url: str, config: dict) -> feedparser.FeedParserDict:
    rss_cfg = config.get("rss", {})
    proxy = rss_cfg.get("proxy")
    timeout = rss_cfg.get("timeout", 15)
    proxies = {"http": proxy, "https": proxy} if proxy else None
    resp = requests.get(
        rss_url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        proxies=proxies,
    )
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def list_episodes(rss_url: str, config: dict, limit: int = 10) -> dict:
    feed = fetch_feed(rss_url, config)
    feed_meta = {
        "title": feed.feed.get("title", ""),
        "publisher": feed.feed.get("author", feed.feed.get("publisher", "")),
        "language": feed.feed.get("language", ""),
    }
    max_shownote = config.get("podcast_tool", {}).get("rss_shownote_max_chars", 4000)
    episodes = [
        normalize_episode_entry(entry, rss_url, max_shownote=max_shownote)["episode"]
        for entry in feed.entries[:limit]
    ]
    return {"feed": feed_meta, "episodes": episodes, "raw_feed": feed}


def find_episode(rss_url: str, episode_id: str, config: dict) -> tuple[dict, dict]:
    feed = fetch_feed(rss_url, config)
    max_shownote = config.get("podcast_tool", {}).get("rss_shownote_max_chars", 4000)
    for entry in feed.entries:
        normalized = normalize_episode_entry(entry, rss_url, max_shownote=max_shownote)
        if normalized["episode"]["episode_id"] == episode_id:
            return normalized["episode"], dict(entry)
    raise LookupError(f"Episode not found: {episode_id}")


def normalize_episode_entry(
    entry: dict,
    rss_url: str = "",
    max_shownote: int = 4000,
) -> dict:
    audio_url = get_audio_url(entry)
    page_url = entry.get("link", "")
    raw_id = entry.get("id") or entry.get("guid") or audio_url or page_url or (
        (entry.get("title", "") or "") + (entry.get("published", "") or "")
    )
    episode_id = stable_episode_id(raw_id)
    shownote = extract_shownote_from_entry(entry)
    shownotes_original = shownote.get("description", "")
    if max_shownote and len(shownotes_original) > max_shownote:
        shownotes_original = shownotes_original[:max_shownote].rstrip()
    episode = {
        "episode_id": episode_id,
        "title": entry.get("title", "episode"),
        "published_at": parse_published(entry.get("published", entry.get("updated", ""))),
        "audio_url": audio_url,
        "page_url": page_url,
        "duration_seconds": parse_duration(entry),
        "shownotes_original": shownotes_original,
        "shownotes_zh": "",
    }
    if rss_url:
        episode["rss_url"] = rss_url
    return {"episode": episode, "entry": dict(entry)}


def stable_episode_id(raw_id: str) -> str:
    raw = str(raw_id or "").strip()
    if not raw:
        raw = "unknown"
    if len(raw) <= 80 and re.match(r"^[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$", raw):
        return raw
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def get_audio_url(entry: dict) -> str:
    enc = entry.get("enclosures", [])
    if enc:
        return enc[0].get("href", "")
    for link in entry.get("links", []):
        if link.get("type", "").startswith("audio"):
            return link.get("href", "")
    return ""


def parse_duration(entry: dict) -> int | None:
    dur = entry.get("itunes_duration", "")
    if dur:
        dur = str(dur).strip()
        if dur.isdigit():
            return int(dur)
        parts = dur.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
        except ValueError:
            return None
    return None


def parse_published(value: str) -> str:
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return value


def _probe_feed(rss_url: str, timeout: int, proxy: str | None) -> dict:
    try:
        proxies = {"http": proxy, "https": proxy} if proxy else None
        resp = requests.get(
            rss_url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            proxies=proxies,
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        return {
            "valid": bool(feed.entries),
            "title": feed.feed.get("title", ""),
            "publisher": feed.feed.get("author", feed.feed.get("publisher", "")),
            "description": _clean_description(feed.feed.get("subtitle", feed.feed.get("description", ""))),
            "language": feed.feed.get("language", ""),
        }
    except Exception:
        return {"valid": False}


def _confidence(query: str, title: str, publisher: str, valid: bool) -> float:
    q = _norm(query)
    title_norm = _norm(title)
    publisher_norm = _norm(publisher)
    score = 0.25 if valid else 0.05
    if q and q == title_norm:
        score += 0.65
    elif q and (q in title_norm or title_norm in q):
        score += 0.45
    elif q and q in publisher_norm:
        score += 0.2
    return min(1.0, round(score, 2))


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").lower()).strip()


def _clean_description(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value or "")
    return text.strip()


def domain_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return ""
