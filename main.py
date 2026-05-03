"""
main.py
=======
播客翻译工作流入口。
交互式选择播客和节目 → 启动翻译工作流。

使用方法:
    python main.py                    # 交互式选择
    python main.py --url <mp3_url>    # 直接指定音频 URL
    python main.py --skip-tts         # 跳过 TTS（只做转写+翻译）
    python main.py --skip-voiceprint         # 跳过声纹提取（只做转写+翻译）
    python main.py --skip-voiceprint --skip-tts         # 跳过声纹提取（只做转写+翻译）
    
    # 用本地文件做测试
    python main.py --local-file /home/zhanghuibin02/code/podcast_translation/output/audio/what_happens_after_coding_is_solved_clip_5min.mp3 --name "podcast" --title "what_happens_after_coding_is_solved_clip_5min" --no-resume
    python main.py --local-file /home/zhanghuibin02/code/podcast_translation/output/audio/what_happens_after_coding_is_solved_clip_head_10min.mp3 --name "podcast" --title "what_happens_after_coding_is_solved_clip_10min" --no-resume
"""

import os
import sys
import argparse
import feedparser
import requests

from core.app_factory import create_providers, load_config
from core.pipeline import Pipeline

# ============================================================
# 已验证有效的 RSS Feed（来自之前的验证工作）
# ============================================================
FEEDS = [
    ("科技", "Lex Fridman Podcast", "https://lexfridman.com/feed/podcast/"),
    ("科技", "Y Combinator", "https://anchor.fm/s/8c1524bc/podcast/rss"),
    ("科技", "a16z Podcast", "https://feeds.simplecast.com/JGE3yC0V"),
    ("科技", "Moonshots", "https://feeds.megaphone.fm/DVVTS2890392624"),
    ("科技", "The MAD Podcast with Matt Turck", "https://anchor.fm/s/f2ee4948/podcast/rss"),
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
    ("商业", "Lenny's Podcast", "https://api.substack.com/feed/podcast/10845.rss"),
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
    ("生命科学", "BrainInspired", "https://braininspired.co/feed/podcast/brain-inspired/"),
    ("生命科学", "Huberman Lab", "https://feeds.megaphone.fm/hubermanlab"),
    ("生命科学", "Science Magazine Podcast", "https://www.science.org/rss/podcast.xml"),
    ("生命科学", "Radiolab", "https://feeds.simplecast.com/EmVW7VGp"),
    ("生命科学", "The Peter Attia Drive", "https://peterattiadrive.libsyn.com/rss"),
    ("生命科学", "The Naked Scientists", "https://www.thenakedscientists.com/naked_scientists_podcast.xml"),
    ("生命科学", "Short Wave (NPR)", "https://feeds.npr.org/510351/podcast.xml"),
    ("生命科学", "Hidden Brain (NPR)", "https://feeds.simplecast.com/kwWQlnMM"),
    ("生命科学", "Life Kit (NPR)", "https://feeds.npr.org/510338/podcast.xml"),
]


# ============================================================
# 时长解析（复用之前脚本的逻辑）
# ============================================================
def parse_duration(entry) -> int | None:
    dur = entry.get("itunes_duration", "")
    if dur:
        dur = str(dur).strip()
        if dur.isdigit():
            return int(dur)
        parts = dur.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
        except ValueError:
            pass
    return None


def fmt_dur(s):
    if s is None:
        return "??:??"
    h, m = s // 3600, (s % 3600) // 60
    return f"{h}h{m:02d}m" if h else f"{m}m{s % 60:02d}s"


def get_audio_url(entry):
    enc = entry.get("enclosures", [])
    if enc:
        return enc[0].get("href", "")
    for link in entry.get("links", []):
        if link.get("type", "").startswith("audio"):
            return link.get("href", "")
    return ""


# ============================================================
# 交互式选择
# ============================================================
def interactive_select(config: dict):
    """交互式选择播客和节目，返回 (podcast_name, episode_title, audio_url, rss_entry)"""

    proxy = config.get("rss", {}).get("proxy")
    timeout = config.get("rss", {}).get("timeout", 15)
    max_eps = config.get("rss", {}).get("max_episodes", 20)

    # Step 1: 选分类
    cats = sorted(set(f[0] for f in FEEDS))
    print("\n  📂 选择分类:")
    print("   0. 全部")
    for i, c in enumerate(cats, 1):
        count = sum(1 for f in FEEDS if f[0] == c)
        print(f"   {i}. {c} ({count})")

    choice = input("\n  输入编号: ").strip()
    if choice.isdigit() and 0 < int(choice) <= len(cats):
        selected_cat = cats[int(choice) - 1]
        show_feeds = [f for f in FEEDS if f[0] == selected_cat]
    else:
        show_feeds = FEEDS

    # Step 2: 选播客
    print(f"\n  🎧 选择播客:")
    for i, (cat, name, url) in enumerate(show_feeds, 1):
        print(f"   {i:2d}. [{cat}] {name}")

    choice = input(f"\n  输入编号 (1-{len(show_feeds)}): ").strip()
    if not choice.isdigit() or int(choice) < 1 or int(choice) > len(show_feeds):
        print("  ❌ 无效选择")
        sys.exit(1)

    cat, podcast_name, rss_url = show_feeds[int(choice) - 1]

    # Step 3: 获取节目列表
    print(f"\n  📡 获取 [{podcast_name}] 的节目列表...")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {"User-Agent": "Mozilla/5.0 (PodcastTranslator/1.0)"}

    try:
        resp = requests.get(rss_url, timeout=timeout, headers=headers, proxies=proxies)
        feed = feedparser.parse(resp.content)
    except Exception as e:
        print(f"  ❌ 获取失败: {e}")
        sys.exit(1)

    if not feed.entries:
        print("  ❌ 未找到节目")
        sys.exit(1)

    # Step 4: 选节目
    entries = feed.entries[:max_eps]
    print(f"\n  📋 最近 {len(entries)} 期:")
    for i, entry in enumerate(entries, 1):
        title = entry.get("title", "")[:60]
        dur = parse_duration(entry)
        dur_str = fmt_dur(dur)
        pub = entry.get("published", "")[:16]
        print(f"   {i:2d}. [{dur_str:>7s}] {title}  ({pub})")

    choice = input(f"\n  选择节目编号 (1-{len(entries)}): ").strip()
    if not choice.isdigit() or int(choice) < 1 or int(choice) > len(entries):
        print("  ❌ 无效选择")
        sys.exit(1)

    entry = entries[int(choice) - 1]
    episode_title = entry.get("title", "unknown")
    audio_url = get_audio_url(entry)

    if not audio_url:
        print("  ❌ 未找到音频链接")
        sys.exit(1)

    print(f"\n  ✅ 已选择:")
    print(f"     播客: {podcast_name}")
    print(f"     节目: {episode_title}")
    print(f"     音频: {audio_url[:80]}...")

    return podcast_name, episode_title, audio_url, entry


# ============================================================
# 主函数
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="播客翻译工作流")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--url", type=str, help="直接指定音频 URL（跳过交互选择）")
    parser.add_argument("--local-file", type=str, help="直接指定本地音频文件路径（跳过下载）")
    parser.add_argument("--name", type=str, default="podcast", help="播客名称（配合 --url 使用）")
    parser.add_argument("--title", type=str, default="episode", help="节目标题（配合 --url 使用）")
    parser.add_argument("--skip-tts", action="store_true", help="跳过 TTS 合成")
    parser.add_argument("--skip-voiceprint", action="store_true", help="跳过声纹提取")
    parser.add_argument("--skip-shownote", action="store_true", help="跳过 Shownote 生成")
    parser.add_argument("--no-resume", action="store_true", help="忽略已有进度，从头开始")
    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)

    print()
    print("╔══════════════════════════════════════════════╗")
    print("║   🎧 播客翻译工作流 Podcast Translator      ║")
    print("║   英文播客 → 中文音频（声音克隆）            ║")
    print("╚══════════════════════════════════════════════╝")

    # 选择节目
    local_audio_path = ""
    rss_entry = None
    if args.local_file:
        import os
        if not os.path.isfile(args.local_file):
            print(f"  ❌ 本地文件不存在: {args.local_file}")
            return
        local_audio_path = os.path.abspath(args.local_file)
        podcast_name = args.name
        episode_title = args.title
        audio_url = f"local://{local_audio_path}"
        print(f"  📂 使用本地文件: {local_audio_path}")
    elif args.url:
        podcast_name = args.name
        episode_title = args.title
        audio_url = args.url
    else:
        podcast_name, episode_title, audio_url, rss_entry = interactive_select(config)

    # 确认启动
    skip_steps = []
    if args.skip_tts:
        skip_steps.append("tts")
    if args.skip_voiceprint:
        skip_steps.append("voiceprint")
    if args.skip_shownote:
        skip_steps.append("shownote")

    print()
    if skip_steps:
        print(f"  ⏭️  跳过步骤: {', '.join(skip_steps)}")

    confirm = input("\n  确认启动工作流？(y/n): ").strip().lower()
    if confirm != "y":
        print("  👋 已取消")
        return

    # 创建 Provider
    stt, llm, tts, storage = create_providers(config)

    # Shownote 专用 LLM（百度千帆）
    shownote_llm = None
    if "shownote" not in skip_steps:
        from providers.baidu_llm import BaiduLLM
        shownote_llm = BaiduLLM(config)

    # 创建进度追踪器
    from core.progress import ProgressTracker

    db_path = config.get("output", {}).get("progress_db", "./data/progress.db")
    progress = ProgressTracker(db_path=db_path)

    episode_id = progress.get_or_create_episode(audio_url, podcast_name, episode_title)

    if args.no_resume:
        progress.reset_episode(episode_id)
        print(f"  🔄 已清除进度记录，将从头开始")
    else:
        # 显示已有进度
        completed = progress.get_completed_steps(episode_id)
        if completed:
            print(f"  📋 已完成步骤: {', '.join(completed.keys())}")
            remaining = [
                s for s in ProgressTracker.STEPS
                if s not in completed and s not in skip_steps
            ]
            print(f"  ▶️  将执行步骤: {', '.join(remaining) if remaining else '全部已完成'}")

    # 创建并运行 Pipeline
    pipeline = Pipeline(config, stt, llm, tts, storage, progress=progress, shownote_llm=shownote_llm)
    ctx = pipeline.run(
        audio_url=audio_url,
        podcast_name=podcast_name,
        episode_title=episode_title,
        skip_steps=skip_steps,
        local_audio_path=local_audio_path,
        rss_entry=rss_entry,
    )

    if progress:
        progress.close()

    # 输出最终结果路径
    print()
    if ctx.transcript_path:
        print(f"  📄 英文转写: {ctx.transcript_path}")
    if ctx.translation_path:
        print(f"  📄 中文翻译: {ctx.translation_path}")
    if ctx.final_audio_path:
        print(f"  🔊 中文音频: {ctx.final_audio_path}")
    if ctx.shownote_path:
        print(f"  📝 Shownote: {ctx.shownote_path}")


if __name__ == "__main__":
    main()
