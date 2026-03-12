"""
RSS Feed 批量验证脚本
=====================
验证 50 个优质英文播客 RSS Feed 的可用性，并输出最新一期节目信息。

使用方法:
    pip install feedparser requests
    python rss_validator.py

可选参数:
    --timeout 15      请求超时秒数（默认 15）
    --output result    输出文件名前缀（默认 result，会生成 .csv 和 .txt）
    --proxy http://127.0.0.1:7890   设置代理（如需科学上网）
"""

import feedparser
import requests
import csv
import time
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# 50 个播客 RSS Feed
# ============================================================
FEEDS = [
    # ===================================================================
    # 科技 Tech (20)
    # ===================================================================
    # ✅ 上轮已验证有效
    ("科技", "Lex Fridman Podcast", "https://lexfridman.com/feed/podcast/"),
    ("科技", "a16z Podcast", "https://feeds.simplecast.com/JGE3yC0V"),
    ("科技", "The Vergecast", "https://feeds.megaphone.fm/vergecast"),
    ("科技", "Hard Fork (NYT)", "https://feeds.simplecast.com/l2i9YnTd"),
    ("科技", "The Changelog", "https://changelog.com/podcast/feed"),
    ("科技", "Darknet Diaries", "https://feeds.megaphone.fm/darknetdiaries"),
    ("科技", "Practical AI", "https://changelog.com/practicalai/feed"),
    # ✅ 第二轮验证有效
    ("科技", "Acquired", "https://acquired.libsyn.com/rss"),
    ("科技", "TWIML (This Week in ML & AI)", "https://twimlai.libsyn.com/rss"),
    # 🔄 第三轮：替换仍失效 / 内容不匹配的链接
    ("科技", "This Week in Startups", "https://thisweekinstartups.libsyn.com/rss"),
    ("科技", "Decoder with Nilay Patel", "https://feeds.megaphone.fm/decoder-ring"),
    ("科技", "Eye on AI → AI Explained", "https://feeds.libsyn.com/482738/rss"),
    ("科技", "The Robot Brains → Machine Learning Street Talk", "https://anchor.fm/s/1e4a0eac/podcast/rss"),
    ("科技", "Gradient Dissent (W&B)", "https://feeds.soundcloud.com/users/soundcloud:users:750099842/sounds.rss"),
    ("科技", "No Priors: AI, ML, Tech", "https://anchor.fm/s/93ebe948/podcast/rss"),
    ("科技", "Tech Won't Save Us", "https://feeds.acast.com/public/shows/tech-wont-save-us"),
    ("科技", "Clockwise → Accidental Tech Podcast", "https://atp.fm/episodes?format=rss"),
    ("科技", "Wired → Ars Technica Podcast", "https://feeds.acast.com/public/shows/arstechnica"),
    ("科技", "Sharp Tech → Pivot (Kara Swisher & Scott Galloway)", "https://feeds.megaphone.fm/pivot-podcast"),
    ("科技", "Latent Space", "https://api.substack.com/feed/podcast/1084089.rss"),

    # ===================================================================
    # 商业 Business (15)
    # ===================================================================
    # ✅ 上轮已验证有效
    ("商业", "How I Built This (NPR)", "https://feeds.npr.org/510313/podcast.xml"),
    ("商业", "Planet Money (NPR)", "https://feeds.npr.org/510289/podcast.xml"),
    ("商业", "Freakonomics Radio", "https://feeds.simplecast.com/Y8lFbOT4"),
    ("商业", "The Indicator (NPR)", "https://feeds.npr.org/510325/podcast.xml"),
    ("商业", "Invest Like the Best", "https://feeds.megaphone.fm/investlikethebest"),
    ("商业", "The Knowledge Project", "https://theknowledgeproject.libsyn.com/rss"),
    # 🔄 第三轮替换
    ("商业", "Masters of Scale", "https://rss.art19.com/masters-of-scale"),
    ("商业", "The All-In Podcast", "https://rss.art19.com/all-in-with-chamath-jason-sacks-and-friedberg"),
    ("商业", "HBR IdeaCast", "https://hbr.org/feed/podcast/ideacast"),
    ("商业", "The Prof G Pod → Prof G Markets", "https://feeds.megaphone.fm/profgmarkets"),
    ("商业", "My First Million", "https://anchor.fm/s/3e84fdd8/podcast/rss"),
    ("商业", "Business Wars", "https://rss.art19.com/business-wars"),
    ("商业", "Odd Lots (Bloomberg)", "https://feeds.bloomberg.com/podcasts/odd-lots"),
    ("商业", "Lenny's Podcast", "https://api.substack.com/feed/podcast/10845.rss"),
    ("商业", "20VC with Harry Stebbings", "https://thetwentyminutevc.libsyn.com/rss"),

    # ===================================================================
    # 生命科学 Life Sciences (15)
    # ===================================================================
    # ✅ 上轮已验证有效
    ("生命科学", "Huberman Lab", "https://feeds.megaphone.fm/hubermanlab"),
    ("生命科学", "Science Magazine Podcast", "https://www.science.org/rss/podcast.xml"),
    ("生命科学", "Radiolab", "https://feeds.simplecast.com/EmVW7VGp"),
    ("生命科学", "The Peter Attia Drive", "https://peterattiadrive.libsyn.com/rss"),
    ("生命科学", "The Naked Scientists", "https://www.thenakedscientists.com/naked_scientists_podcast.xml"),
    # 🔄 第三轮替换
    ("生命科学", "Nature Podcast", "https://www.nature.com/nature.rss"),
    ("生命科学", "TWIV (This Week in Virology)", "https://www.microbe.tv/twiv/feed/"),
    ("生命科学", "The Long Run → The Readout Loud (STAT News)", "https://feeds.megaphone.fm/STM8794490131"),
    ("生命科学", "NEJM Interviews", "https://feeds.libsyn.com/481249/rss"),
    ("生命科学", "The Lancet Voice", "https://www.buzzsprout.com/2223020.rss"),
    ("生命科学", "Cell Press → iBiology Podcast", "https://feeds.libsyn.com/345927/rss"),
    ("生命科学", "Genetics Unzipped", "https://feeds.libsyn.com/325498/rss"),
    ("生命科学", "BioTech Startup Podcast", "https://anchor.fm/s/45fc8304/podcast/rss"),
    ("生命科学", "Mendelspod → Genomics and Beyond", "https://feeds.libsyn.com/430441/rss"),
    ("生命科学", "BioCentury → The Pulse (WHYY Health)", "https://feeds.simplecast.com/MFzLsfgd"),
]


def validate_feed(cat, name, url, timeout=15, proxy=None):
    """验证单个 RSS Feed，返回结果字典"""
    result = {
        "分类": cat,
        "播客名称": name,
        "RSS链接": url,
        "状态": "❌ 失败",
        "HTTP状态码": "",
        "最新一期标题": "",
        "最新发布日期": "",
        "音频链接示例": "",
        "总集数": "",
        "错误信息": "",
    }

    proxies = {"http": proxy, "https": proxy} if proxy else None

    try:
        # 第一步：HTTP 请求验证
        headers = {
            "User-Agent": "Mozilla/5.0 (PodcastValidator/1.0; +https://github.com)"
        }
        resp = requests.get(url, timeout=timeout, headers=headers, proxies=proxies, allow_redirects=True)
        result["HTTP状态码"] = resp.status_code

        if resp.status_code != 200:
            result["错误信息"] = f"HTTP {resp.status_code}"
            return result

        # 第二步：解析 RSS
        feed = feedparser.parse(resp.content)

        if feed.bozo and not feed.entries:
            result["状态"] = "⚠️ 格式异常"
            result["错误信息"] = str(feed.bozo_exception)[:80]
            return result

        if not feed.entries:
            result["状态"] = "⚠️ 无节目"
            result["错误信息"] = "Feed 可访问但没有找到任何节目条目"
            return result

        # 解析成功
        result["状态"] = "✅ 有效"
        result["总集数"] = len(feed.entries)

        # 最新一期
        latest = feed.entries[0]
        result["最新一期标题"] = latest.get("title", "")[:80]

        # 发布日期
        pub = latest.get("published", latest.get("updated", ""))
        if pub:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub)
                result["最新发布日期"] = dt.strftime("%Y-%m-%d")
            except Exception:
                result["最新发布日期"] = pub[:20]

        # 提取音频链接
        enclosures = latest.get("enclosures", [])
        if enclosures:
            result["音频链接示例"] = enclosures[0].get("href", "")[:150]
        else:
            for link in latest.get("links", []):
                if link.get("type", "").startswith("audio"):
                    result["音频链接示例"] = link.get("href", "")[:150]
                    break

    except requests.exceptions.Timeout:
        result["错误信息"] = "请求超时"
    except requests.exceptions.ConnectionError as e:
        result["错误信息"] = f"连接失败: {str(e)[:60]}"
    except Exception as e:
        result["错误信息"] = f"{type(e).__name__}: {str(e)[:60]}"

    return result


def print_result(i, total, r):
    """打印单条结果"""
    status = r["状态"]
    name = r["播客名称"]
    cat = r["分类"]
    print(f"  [{i:02d}/{total}] {status}  [{cat}] {name}")
    if r["最新一期标题"]:
        print(f"          最新: {r['最新一期标题']}")
        if r["最新发布日期"]:
            print(f"          日期: {r['最新发布日期']}  |  总集数: {r['总集数']}")
    if r["错误信息"]:
        print(f"          错误: {r['错误信息']}")


def main():
    parser = argparse.ArgumentParser(description="RSS Feed 批量验证工具")
    parser.add_argument("--timeout", type=int, default=15, help="请求超时秒数 (默认 15)")
    parser.add_argument("--output", type=str, default="result", help="输出文件名前缀 (默认 result)")
    parser.add_argument("--proxy", type=str, default=None, help="代理地址，如 http://127.0.0.1:7890")
    parser.add_argument("--workers", type=int, default=5, help="并发数 (默认 5)")
    args = parser.parse_args()

    total = len(FEEDS)
    print("=" * 60)
    print(f"  🎧 RSS Feed 批量验证工具")
    print(f"  共 {total} 个 Feed | 超时 {args.timeout}s | 并发 {args.workers}")
    if args.proxy:
        print(f"  代理: {args.proxy}")
    print("=" * 60)
    print()

    results = [None] * total
    start_time = time.time()
    done_count = 0

    def task(index):
        cat, name, url = FEEDS[index]
        return index, validate_feed(cat, name, url, args.timeout, args.proxy)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(task, i): i for i in range(total)}
        for future in as_completed(futures):
            idx, result = future.result()
            results[idx] = result
            done_count += 1
            print_result(done_count, total, result)

    elapsed = time.time() - start_time

    # ========== 统计 ==========
    ok = [r for r in results if "✅" in r["状态"]]
    warn = [r for r in results if "⚠️" in r["状态"]]
    fail = [r for r in results if "❌" in r["状态"]]

    print()
    print("=" * 60)
    print(f"  验证完成！耗时 {elapsed:.1f}s")
    print(f"  ✅ 有效: {len(ok)}  |  ⚠️ 异常: {len(warn)}  |  ❌ 失败: {len(fail)}")
    print("=" * 60)

    # ========== 输出失败列表 ==========
    if fail or warn:
        print()
        print("  ⚠️ 需要关注的 Feed:")
        for r in warn + fail:
            print(f"    {r['状态']}  {r['播客名称']}: {r['错误信息']}")

    # ========== 导出 CSV ==========
    csv_path = f"{args.output}.csv"
    fields = ["分类", "播客名称", "RSS链接", "状态", "HTTP状态码", "最新一期标题", "最新发布日期", "音频链接示例", "总集数", "错误信息"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
    print(f"\n  📄 CSV 报告已保存: {csv_path}")

    # ========== 导出有效 RSS 纯链接 ==========
    txt_path = f"{args.output}_valid_rss.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        for r in results:
            if "✅" in r["状态"]:
                f.write(r["RSS链接"] + "\n")
    print(f"  📄 有效 RSS 链接已保存: {txt_path}")

    # ========== 导出 OPML ==========
    opml_path = f"{args.output}.opml"
    with open(opml_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<opml version="2.0">\n<head><title>Validated Podcast Feeds</title></head>\n<body>\n')
        for r in results:
            if "✅" in r["状态"]:
                safe_name = r["播客名称"].replace("&", "&amp;").replace('"', "&quot;")
                f.write(f'  <outline text="{safe_name}" title="{safe_name}" type="rss" xmlUrl="{r["RSS链接"]}" />\n')
        f.write('</body>\n</opml>\n')
    print(f"  📄 有效 Feed OPML 已保存: {opml_path}")

    print()


if __name__ == "__main__":
    main()