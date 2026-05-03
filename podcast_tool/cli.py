import argparse
import os
import sys

from core.app_factory import load_config

from podcast_tool import rss
from podcast_tool.jsonio import (
    EXIT_CONFIG,
    EXIT_INVALID_ARGUMENT,
    EXIT_NOT_FOUND,
    EXIT_OK,
    ToolError,
    unexpected_error,
    write_error,
    write_json,
)
from podcast_tool.process import is_pid_running, start_worker, terminate_process
from podcast_tool.state import TranslationStore


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if getattr(args, "json", False) is not True:
            raise ToolError(
                "INVALID_ARGUMENT",
                "All podcast_tool commands must include --json.",
                EXIT_INVALID_ARGUMENT,
                retryable=False,
            )
        if args.resource == "rss" and args.action == "find":
            return cmd_rss_find(args)
        if args.resource == "episodes" and args.action == "list":
            return cmd_episodes_list(args)
        if args.resource == "translate" and args.action == "start":
            return cmd_translate_start(args)
        if args.resource == "translate" and args.action == "status":
            return cmd_translate_status(args)
        if args.resource == "translate" and args.action == "list":
            return cmd_translate_list(args)
        if args.resource == "translate" and args.action == "cancel":
            return cmd_translate_cancel(args)
        raise ToolError("INVALID_ARGUMENT", "Unknown command.", EXIT_INVALID_ARGUMENT)
    except ToolError as exc:
        return write_error(exc)
    except Exception as exc:
        return unexpected_error(exc)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="podcast_tool")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    sub = parser.add_subparsers(dest="resource", required=True, parser_class=JsonArgumentParser)

    rss_parser = sub.add_parser("rss")
    rss_sub = rss_parser.add_subparsers(dest="action", required=True, parser_class=JsonArgumentParser)
    rss_find = rss_sub.add_parser("find")
    rss_find.add_argument("--query", required=True)
    rss_find.add_argument("--limit", type=int, default=10)
    rss_find.add_argument("--json", action="store_true")

    episodes_parser = sub.add_parser("episodes")
    episodes_sub = episodes_parser.add_subparsers(dest="action", required=True, parser_class=JsonArgumentParser)
    episodes_list = episodes_sub.add_parser("list")
    episodes_list.add_argument("--rss-url", required=True)
    episodes_list.add_argument("--limit", type=int, default=10)
    episodes_list.add_argument("--json", action="store_true")

    translate_parser = sub.add_parser("translate")
    translate_sub = translate_parser.add_subparsers(dest="action", required=True, parser_class=JsonArgumentParser)

    start = translate_sub.add_parser("start")
    start.add_argument("--rss-url")
    start.add_argument("--episode-id")
    start.add_argument("--audio-url")
    start.add_argument("--local-file")
    start.add_argument("--title", default="episode")
    start.add_argument("--page-url", default="")
    start.add_argument("--target-lang", default="zh-CN")
    start.add_argument("--voice-clone", default="true")
    start.add_argument("--skip-tts", action="store_true")
    start.add_argument("--skip-shownote", action="store_true")
    start.add_argument("--force", action="store_true")
    start.add_argument("--json", action="store_true")

    status = translate_sub.add_parser("status")
    status.add_argument("--job-id", required=True)
    status.add_argument("--json", action="store_true")

    list_cmd = translate_sub.add_parser("list")
    list_cmd.add_argument("--status", default="active")
    list_cmd.add_argument("--json", action="store_true")

    cancel = translate_sub.add_parser("cancel")
    cancel.add_argument("--job-id", required=True)
    cancel.add_argument("--json", action="store_true")

    return parser


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ToolError("INVALID_ARGUMENT", message, EXIT_INVALID_ARGUMENT, retryable=False)


def cmd_rss_find(args) -> int:
    config = load_config(args.config, quiet=True)
    feeds = rss.find_feeds(args.query, config, limit=args.limit)
    return write_json({"ok": True, "query": args.query, "feeds": feeds, "error": None})


def cmd_episodes_list(args) -> int:
    config = load_config(args.config, quiet=True)
    result = rss.list_episodes(args.rss_url, config, limit=args.limit)
    return write_json({
        "ok": True,
        "rss_url": args.rss_url,
        "feed": result["feed"],
        "episodes": result["episodes"],
        "error": None,
    })


def cmd_translate_start(args) -> int:
    config_path = os.path.abspath(args.config)
    config = load_config(config_path, quiet=True)
    episode = resolve_start_episode(args, config)
    voice_clone = parse_bool(args.voice_clone)
    skip_steps = []
    if not voice_clone:
        skip_steps.append("voiceprint")
    if args.skip_tts:
        skip_steps.append("tts")
    if args.skip_shownote:
        skip_steps.append("shownote")

    store = TranslationStore(config, config_path=config_path)
    try:
        job, created = store.create_or_get_translation(
            episode=episode,
            target_lang=args.target_lang,
            voice_clone=voice_clone,
            skip_steps=skip_steps,
            force=args.force,
        )
        if created:
            try:
                pid = start_worker(
                    job_id=job["job_id"],
                    config_path=config_path,
                    log_path=job["artifacts"]["log"],
                    cwd=repo_root(),
                )
                store.update(job["job_id"], pid=pid)
                job = store.get(job["job_id"])
            except Exception as exc:
                error = {"code": "WORKER_START_ERROR", "message": str(exc), "retryable": True}
                store.update(
                    job["job_id"],
                    status="failed",
                    stage="failed",
                    message=error["message"],
                    error=error,
                    finished=True,
                )
                raise ToolError("WORKER_START_ERROR", str(exc), EXIT_CONFIG, retryable=True)
        return write_json({"ok": True, "job": job, "error": None})
    finally:
        store.close()


def cmd_translate_status(args) -> int:
    config = load_config(args.config, quiet=True)
    store = TranslationStore(config, config_path=args.config)
    try:
        job = store.get(args.job_id)
        if not job:
            raise ToolError("NOT_FOUND", f"Translation job not found: {args.job_id}", EXIT_NOT_FOUND)
        job = mark_worker_lost_if_needed(store, job)
        ok = job["status"] != "failed"
        return write_json({"ok": ok, "job": job, "error": job.get("error")})
    finally:
        store.close()


def cmd_translate_list(args) -> int:
    config = load_config(args.config, quiet=True)
    store = TranslationStore(config, config_path=args.config)
    try:
        jobs = [mark_worker_lost_if_needed(store, job) for job in store.list(args.status)]
        return write_json({"ok": True, "jobs": jobs, "error": None})
    finally:
        store.close()


def cmd_translate_cancel(args) -> int:
    config = load_config(args.config, quiet=True)
    store = TranslationStore(config, config_path=args.config)
    try:
        job = store.get(args.job_id)
        if not job:
            raise ToolError("NOT_FOUND", f"Translation job not found: {args.job_id}", EXIT_NOT_FOUND)
        terminate_process(job.get("pid"))
        job = store.cancel(args.job_id)
        return write_json({"ok": True, "job": job, "error": None})
    finally:
        store.close()


def resolve_start_episode(args, config: dict) -> dict:
    if args.rss_url or args.episode_id:
        if not args.rss_url or not args.episode_id:
            raise ToolError(
                "INVALID_ARGUMENT",
                "--rss-url and --episode-id must be provided together.",
                EXIT_INVALID_ARGUMENT,
            )
        try:
            episode, _entry = rss.find_episode(args.rss_url, args.episode_id, config)
            return episode
        except LookupError as exc:
            raise ToolError("NOT_FOUND", str(exc), EXIT_NOT_FOUND, retryable=False) from exc

    audio_url = args.audio_url
    if args.local_file:
        if not os.path.isfile(args.local_file):
            raise ToolError(
                "NOT_FOUND",
                f"Local audio file not found: {args.local_file}",
                EXIT_NOT_FOUND,
            )
        audio_url = "local://" + os.path.abspath(args.local_file)
    if not audio_url:
        raise ToolError(
            "INVALID_ARGUMENT",
            "Either --rss-url/--episode-id, --audio-url, or --local-file is required.",
            EXIT_INVALID_ARGUMENT,
        )
    return {
        "episode_id": rss.stable_episode_id(audio_url),
        "title": args.title or "episode",
        "audio_url": audio_url,
        "page_url": args.page_url or "",
        "published_at": "",
        "rss_url": "",
    }


def mark_worker_lost_if_needed(store: TranslationStore, job: dict) -> dict:
    if job["status"] in {"queued", "running"} and job.get("pid") and not is_pid_running(job.get("pid")):
        error = {
            "code": "WORKER_LOST",
            "message": "Background translation worker is no longer running.",
            "retryable": True,
        }
        store.update(
            job["job_id"],
            status="failed",
            stage="failed",
            message=error["message"],
            error=error,
            finished=True,
        )
        job = store.get(job["job_id"])
    return job


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


if __name__ == "__main__":
    sys.exit(main())
