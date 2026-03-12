"""播客翻译工具 CLI 入口。

使用方式:
    # 列出 RSS feed 中的 episode
    python main.py --feed <rss_url> --list

    # 翻译最新 N 个 episode
    python main.py --feed <rss_url> --episodes 3
    python main.py --feed <https://feeds.npr.org/510289/podcast.xml> --episodes 1

    # 使用 config.yaml 中配置的所有 feed
    python main.py --all

    # 使用本地 MP3 文件测试完整工作流
    python main.py test --file /path/to/audio.mp3
    python main.py test --file /path/to/audio.mp3 --name "播客名" --title "节目标题"

    # 查看处理进度
    python main.py --status
"""

import argparse
import sys

from loguru import logger


def setup():
    """初始化日志和配置。"""
    from utils.logger import setup_logger

    setup_logger()
    from config import load_settings

    return load_settings()


def cmd_list(args):
    """列出 RSS feed 中的 episode 列表。"""
    from rss.feed_parser import fetch_episodes

    episodes = fetch_episodes(args.feed)
    print(f"\n{'=' * 60}")
    print(f"播客: {episodes[0].podcast_name if episodes else 'N/A'}")
    print(f"共 {len(episodes)} 个 episode")
    print(f"{'=' * 60}\n")

    for i, ep in enumerate(episodes[:20], 1):
        duration = (
            f"{ep.duration_seconds // 60}分钟" if ep.duration_seconds else "未知时长"
        )
        print(f"  {i:2d}. [{ep.published_date.strftime('%Y-%m-%d')}] {ep.title}")
        print(f"      时长: {duration}  格式: {ep.audio_type}")
        print(f"      音频: {ep.audio_url[:80]}...")
        print()


def cmd_translate(args):
    """翻译指定 feed 的 episode。"""
    settings = setup()
    from pipeline.orchestrator import PodcastPipeline

    pipeline = PodcastPipeline(settings)
    pipeline.process_feed(args.feed, max_episodes=args.episodes)


def cmd_all(args):
    """翻译 config.yaml 中所有 feed。"""
    settings = setup()
    from pipeline.orchestrator import PodcastPipeline

    if not settings.rss_feeds:
        print("config.yaml 中未配置 RSS feed，请先添加。")
        sys.exit(1)

    pipeline = PodcastPipeline(settings)
    for feed_url in settings.rss_feeds:
        try:
            pipeline.process_feed(feed_url)
        except Exception as e:
            logger.error(f"Feed 处理失败: {feed_url} - {e}")


def cmd_test(args):
    """使用本地 MP3 文件测试完整工作流。"""
    settings = setup()
    from pipeline.orchestrator import PodcastPipeline

    pipeline = PodcastPipeline(settings)
    pipeline.process_local_file(
        audio_path=args.file,
        podcast_name=args.name,
        episode_title=args.title,
    )


def cmd_status(args):
    """查看处理进度。"""
    from pipeline.progress_tracker import ProgressTracker

    tracker = ProgressTracker()
    records = tracker.list_all()
    tracker.close()

    if not records:
        print("暂无处理记录。")
        return

    print(f"\n{'状态':<12} {'播客':<20} {'标题':<30} {'更新时间'}")
    print("-" * 80)
    for r in records:
        status_icon = {
            "completed": "✓",
            "failed": "✗",
            "pending": "○",
        }.get(r["status"], "●")
        print(
            f"  {status_icon} {r['status']:<10} {(r['podcast_name'] or '')[:18]:<20} "
            f"{(r['title'] or '')[:28]:<30} {r['updated_at'] or ''}"
        )
    print()


def main():
    parser = argparse.ArgumentParser(
        description="英文播客自动翻译为中文（含声音克隆）"
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # list 命令
    list_parser = subparsers.add_parser("list", help="列出 RSS feed 中的 episode")
    list_parser.add_argument("--feed", required=True, help="RSS feed URL")

    # translate 命令
    trans_parser = subparsers.add_parser("translate", help="翻译指定 feed")
    trans_parser.add_argument("--feed", required=True, help="RSS feed URL")
    trans_parser.add_argument(
        "--episodes", type=int, default=1, help="处理的 episode 数量（默认 1）"
    )

    # all 命令
    subparsers.add_parser("all", help="翻译 config.yaml 中所有 feed")

    # test 命令
    test_parser = subparsers.add_parser("test", help="使用本地 MP3 文件测试完整工作流")
    test_parser.add_argument("--file", required=True, help="本地 MP3 文件路径")
    test_parser.add_argument("--name", default="本地测试", help="播客名称（默认: 本地测试）")
    test_parser.add_argument("--title", default="本地音频测试", help="节目标题（默认: 本地音频测试）")

    # status 命令
    subparsers.add_parser("status", help="查看处理进度")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    # 执行对应命令
    commands = {
        "list": cmd_list,
        "translate": cmd_translate,
        "all": cmd_all,
        "test": cmd_test,
        "status": cmd_status,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
