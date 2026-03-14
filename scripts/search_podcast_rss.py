"""
通过 Apple Podcasts Search API 查找播客的真实 RSS Feed 地址
============================================================
Apple 的搜索接口是公开免费的，不需要 API Key。

用法:
    python search_podcast_rss.py "Y Combinator"
    python search_podcast_rss.py "Garry Tan"
    python search_podcast_rss.py "startup school"
"""

import sys
import requests
import feedparser
import json


def search_apple_podcasts(query: str, limit: int = 10) -> list[dict]:
    """通过 Apple Podcasts API 搜索播客"""
    url = "https://itunes.apple.com/search"
    params = {
        "term": query,
        "media": "podcast",
        "limit": limit,
        "country": "US",
    }
    resp = requests.get(url, params=params, timeout=15)
    data = resp.json()
    return data.get("results", [])


def verify_rss(rss_url: str, timeout: int = 10, max_stale_days: int = 30) -> dict | None:
    """
    验证 RSS 是否有效。
    只有最近 max_stale_days 天内有更新的才算「活跃」。

    Returns:
        dict: 验证信息（含 active 字段标记是否活跃）
        None: 请求失败或无节目
    """
    try:
        resp = requests.get(rss_url, timeout=timeout,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return None

        feed = feedparser.parse(resp.content)
        if not feed.entries:
            return None

        latest = feed.entries[0]
        dur = latest.get("itunes_duration", "")

        # 解析最新一期发布日期
        pub_str = latest.get("published", latest.get("updated", ""))
        active = False
        days_ago = None
        pub_date = ""

        if pub_str:
            try:
                from email.utils import parsedate_to_datetime
                pub_dt = parsedate_to_datetime(pub_str)
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                days_ago = (now - pub_dt).days
                active = days_ago <= max_stale_days
                pub_date = pub_dt.strftime("%Y-%m-%d")
            except Exception:
                pub_date = pub_str[:25]

        return {
            "title": latest.get("title", "")[:70],
            "date": pub_date,
            "duration": str(dur),
            "episodes": len(feed.entries),
            "active": active,
            "days_ago": days_ago,
        }
    except Exception:
        return None


def main():
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Y Combinator"

    print(f"\n  🔍 搜索播客: \"{query}\"\n")
    print("─" * 70)

    results = search_apple_podcasts(query)

    if not results:
        print("  未找到结果")
        return

    for i, r in enumerate(results, 1):
        name = r.get("collectionName", "")
        artist = r.get("artistName", "")
        rss = r.get("feedUrl", "")
        apple_url = r.get("collectionViewUrl", "")
        genre = ", ".join(r.get("genres", []))

        print(f"  {i:2d}. {name}")
        print(f"      作者: {artist}")
        print(f"      分类: {genre}")

        if rss:
            print(f"      RSS:  {rss}")
            info = verify_rss(rss)
            if info:
                status = "✅ 活跃" if info["active"] else "💤 停更"
                days_str = f"{info['days_ago']}天前" if info["days_ago"] is not None else "未知"
                print(f"      {status} | 共 {info['episodes']} 期 | 最新更新: {days_str}")
                print(f"         最新: {info['title']}")
                print(f"         发布: {info['date']} | 时长: {info['duration']}")
            else:
                print(f"      ❌ RSS 无法访问或无节目")
        else:
            print(f"      ⚠️ 未提供 RSS")

        print(f"      Apple: {apple_url}")
        print()

    # 导出有效的 RSS（只要活跃的）
    print("─" * 70)
    print("  ✅ 活跃播客 RSS（最近 30 天有更新）:")
    active_count = 0
    for r in results:
        rss = r.get("feedUrl", "")
        name = r.get("collectionName", "")
        if rss:
            info = verify_rss(rss)
            if info and info["active"]:
                active_count += 1
                print(f"    {active_count}. {name} ({info['days_ago']}天前更新)")
                print(f"       {rss}")

    if not active_count:
        print("    无活跃播客")

    stale = []
    for r in results:
        rss = r.get("feedUrl", "")
        name = r.get("collectionName", "")
        if rss:
            info = verify_rss(rss)
            if info and not info["active"]:
                days = info["days_ago"] if info["days_ago"] is not None else "?"
                stale.append((name, rss, days))

    if stale:
        print(f"\n  💤 已停更（超过 30 天未更新）:")
        for name, rss, days in stale:
            print(f"    - {name} ({days}天前)")
            print(f"      {rss}")


if __name__ == "__main__":
    main()