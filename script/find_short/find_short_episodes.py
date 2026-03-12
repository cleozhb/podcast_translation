"""
查找短播客节目用于工作流测试
==============================
从已验证有效的 RSS Feed 中扫描最近的短节目（默认 < 15 分钟）。

使用方法:
    pip install feedparser requests mutagen
    
    找短的
    python find_short_episodes.py
    
    找 10 分钟以内的并自动下载最短的：    
    python find_short_episodes.py --max-minutes 10 --download

    扫描每个播客最近 10 期，扩大搜索范围：
    python find_short_episodes.py --max-minutes 15 --recent 10 --download

可选参数:
    --max-minutes 10     最大时长（分钟，默认 15）
    --recent 5           每个 Feed 扫描最近 N 期（默认 5）
    --download           自动下载最短的那一期
    --proxy http://127.0.0.1:7890
"""

import feedparser
import requests
import argparse
import re
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ============================================================
# 已验证有效的 RSS Feed
# ============================================================
FEEDS = [
    ("科技", "Lex Fridman Podcast", "https://lexfridman.com/feed/podcast/"),
    ("科技", "a16z Podcast", "https://feeds.simplecast.com/JGE3yC0V"),
    ("科技", "The Vergecast", "https://feeds.megaphone.fm/vergecast"),
    ("科技", "Hard Fork (NYT)", "https://feeds.simplecast.com/l2i9YnTd"),
    ("科技", "The Changelog", "https://changelog.com/podcast/feed"),
    ("科技", "Darknet Diaries", "https://feeds.megaphone.fm/darknetdiaries"),
    ("科技", "Practical AI", "https://changelog.com/practicalai/feed"),
    ("科技", "Acquired", "https://acquired.libsyn.com/rss"),
    ("科技", "TWIML", "https://twimlai.libsyn.com/rss"),
    ("科技", "Machine Learning Street Talk", "https://anchor.fm/s/1e4a0eac/podcast/rss"),
    ("科技", "Accidental Tech Podcast", "https://atp.fm/episodes?format=rss"),
    ("科技", "Latent Space", "https://api.substack.com/feed/podcast/1084089.rss"),
    ("商业", "How I Built This (NPR)", "https://feeds.npr.org/510313/podcast.xml"),
    ("商业", "Planet Money (NPR)", "https://feeds.npr.org/510289/podcast.xml"),
    ("商业", "Freakonomics Radio", "https://feeds.simplecast.com/Y8lFbOT4"),
    ("商业", "The Indicator (NPR)", "https://feeds.npr.org/510325/podcast.xml"),
    ("商业", "Invest Like the Best", "https://feeds.megaphone.fm/investlikethebest"),
    ("商业", "The Knowledge Project", "https://theknowledgeproject.libsyn.com/rss"),
    ("商业", "Masters of Scale", "https://rss.art19.com/masters-of-scale"),
    ("商业", "Prof G Markets", "https://feeds.megaphone.fm/profgmarkets"),
    ("商业", "Business Wars", "https://rss.art19.com/business-wars"),
    ("商业", "20VC with Harry Stebbings", "https://thetwentyminutevc.libsyn.com/rss"),
    ("商业", "Lenny's Podcast", "https://www.lennysnewsletter.com/feed"),
    ("生命科学", "Huberman Lab", "https://feeds.megaphone.fm/hubermanlab"),
    ("生命科学", "Science Magazine Podcast", "https://www.science.org/rss/podcast.xml"),
    ("生命科学", "Radiolab", "https://feeds.simplecast.com/EmVW7VGp"),
    ("生命科学", "The Peter Attia Drive", "https://peterattiadrive.libsyn.com/rss"),
    ("生命科学", "The Naked Scientists", "https://www.thenakedscientists.com/naked_scientists_podcast.xml"),
    ("生命科学", "Short Wave (NPR Science)", "https://feeds.npr.org/510351/podcast.xml"),
    ("生命科学", "Hidden Brain (NPR)", "https://feeds.simplecast.com/kwWQlnMM"),
    ("生命科学", "Life Kit (NPR Health)", "https://feeds.npr.org/510338/podcast.xml"),
]


def parse_duration(entry):
    """从 RSS 条目中提取时长（秒）。尝试多种字段。"""

    # 方法1: itunes:duration（最常见）
    dur = entry.get("itunes_duration", "")
    if dur:
        dur = str(dur).strip()
        # 纯秒数
        if dur.isdigit():
            return int(dur)
        # HH:MM:SS 或 MM:SS
        parts = dur.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
        except ValueError:
            pass

    # 方法2: enclosure 文件大小估算 (128kbps ≈ 960KB/min)
    enclosures = entry.get("enclosures", [])
    if enclosures:
        length = enclosures[0].get("length", "")
        if length and str(length).isdigit() and int(length) > 0:
            size_mb = int(length) / (1024 * 1024)
            estimated_min = size_mb / 0.96  # 128kbps
            return int(estimated_min * 60)

    return None


def format_duration(seconds):
    """格式化秒数为可读字符串"""
    if seconds is None:
        return "未知"
    h, m = seconds // 3600, (seconds % 3600) // 60
    if h > 0:
        return f"{h}h{m:02d}m"
    return f"{m}m{seconds % 60:02d}s"


def get_audio_url(entry):
    """提取音频下载链接"""
    enclosures = entry.get("enclosures", [])
    if enclosures:
        return enclosures[0].get("href", "")
    for link in entry.get("links", []):
        if link.get("type", "").startswith("audio"):
            return link.get("href", "")
    return ""


def scan_feed(cat, name, url, max_minutes, recent, timeout, proxy):
    """扫描单个 Feed，返回短节目列表"""
    results = []
    proxies = {"http": proxy, "https": proxy} if proxy else None

    try:
        headers = {"User-Agent": "Mozilla/5.0 (PodcastFinder/1.0)"}
        resp = requests.get(url, timeout=timeout, headers=headers, proxies=proxies)
        if resp.status_code != 200:
            return results

        feed = feedparser.parse(resp.content)
        if not feed.entries:
            return results

        for entry in feed.entries[:recent]:
            dur = parse_duration(entry)
            if dur is not None and dur <= max_minutes * 60:
                pub = entry.get("published", entry.get("updated", ""))
                pub_date = ""
                if pub:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_date = parsedate_to_datetime(pub).strftime("%Y-%m-%d")
                    except Exception:
                        pub_date = pub[:16]

                results.append({
                    "分类": cat,
                    "播客": name,
                    "标题": entry.get("title", "")[:80],
                    "时长秒": dur,
                    "时长": format_duration(dur),
                    "日期": pub_date,
                    "音频URL": get_audio_url(entry),
                })

    except Exception:
        pass

    return results


def download_episode(episode, proxy=None):
    """下载一期节目"""
    url = episode["音频URL"]
    if not url:
        print("  ❌ 无音频链接，无法下载")
        return

    # 生成文件名
    safe_name = re.sub(r'[\\/*?:"<>|]', "", episode["标题"])[:60].strip()
    filename = f"{safe_name}.mp3"

    print(f"\n  📥 正在下载: {filename}")
    print(f"     来源: {episode['播客']}")
    print(f"     链接: {url[:100]}...")

    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {"User-Agent": "Mozilla/5.0 (PodcastDownloader/1.0)"}

    try:
        resp = requests.get(url, stream=True, timeout=60, headers=headers, proxies=proxies, allow_redirects=True)
        total = int(resp.headers.get("content-length", 0))

        downloaded = 0
        with open(filename, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = downloaded / total * 100
                    bar = "█" * int(pct // 4) + "░" * (25 - int(pct // 4))
                    print(f"\r     [{bar}] {pct:.1f}% ({downloaded // 1024}KB / {total // 1024}KB)", end="")

        size_mb = os.path.getsize(filename) / (1024 * 1024)
        print(f"\n\n  ✅ 下载完成: {filename} ({size_mb:.1f}MB)")
        print(f"     可以用这个文件测试你的工作流了！")
        return filename

    except Exception as e:
        print(f"\n  ❌ 下载失败: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="查找短播客节目")
    parser.add_argument("--max-minutes", type=int, default=15, help="最大时长分钟 (默认 15)")
    parser.add_argument("--recent", type=int, default=5, help="每个 Feed 扫描最近 N 期 (默认 5)")
    parser.add_argument("--timeout", type=int, default=15, help="请求超时秒数 (默认 15)")
    parser.add_argument("--proxy", type=str, default=None, help="代理地址")
    parser.add_argument("--download", action="store_true", help="自动下载最短的一期")
    parser.add_argument("--workers", type=int, default=5, help="并发数 (默认 5)")
    args = parser.parse_args()

    print("=" * 65)
    print(f"  🔍 查找短播客节目（≤ {args.max_minutes} 分钟）")
    print(f"  扫描 {len(FEEDS)} 个 Feed，每个最近 {args.recent} 期")
    print("=" * 65)
    print()

    all_episodes = []

    def task(f):
        return scan_feed(*f, args.max_minutes, args.recent, args.timeout, args.proxy)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(task, f): f[1] for f in FEEDS}
        done = 0
        for future in as_completed(futures):
            done += 1
            name = futures[future]
            eps = future.result()
            if eps:
                all_episodes.extend(eps)
                print(f"  [{done:02d}/{len(FEEDS)}] {name}: 找到 {len(eps)} 期短节目")
            else:
                print(f"  [{done:02d}/{len(FEEDS)}] {name}: 无短节目")

    if not all_episodes:
        print("\n  😕 未找到符合条件的短节目。试试增大 --max-minutes？")
        return

    # 按时长排序
    all_episodes.sort(key=lambda x: x["时长秒"])

    print()
    print("=" * 65)
    print(f"  🎯 找到 {len(all_episodes)} 期 ≤ {args.max_minutes} 分钟的节目")
    print("=" * 65)
    print()

    # 显示前 20 个最短的
    show_count = min(20, len(all_episodes))
    print(f"  按时长排序，最短的 {show_count} 期：")
    print(f"  {'─' * 60}")

    for i, ep in enumerate(all_episodes[:show_count]):
        marker = " 👈 推荐" if i == 0 else ""
        print(f"  {i + 1:2d}. [{ep['时长']:>7s}] [{ep['分类']}] {ep['播客']}")
        print(f"      {ep['标题']}")
        print(f"      📅 {ep['日期']}  🔗 {ep['音频URL'][:80]}...")
        if marker:
            print(f"      {marker}")
        print()

    # 自动下载最短的
    if args.download:
        best = all_episodes[0]
        print(f"{'─' * 65}")
        download_episode(best, args.proxy)
    else:
        print(f"  💡 提示: 加 --download 参数可以自动下载最短的那期")
        print(f"     python find_short_episodes.py --download")


if __name__ == "__main__":
    main()