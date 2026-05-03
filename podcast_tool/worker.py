import argparse
import sys

from podcast_tool.runner import run_translation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Podcast translation background worker")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--job-id", required=True)
    run_parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)

    if args.command == "run":
        return run_translation(args.job_id, args.config)
    return 2


if __name__ == "__main__":
    sys.exit(main())
