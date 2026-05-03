import hashlib
import json
import os
from datetime import datetime, timezone

from core.progress import ProgressTracker


ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class TranslationStore:
    def __init__(self, config: dict, config_path: str = "config.yaml"):
        self.config = config
        self.config_path = config_path
        db_path = config.get("output", {}).get("progress_db", "./data/progress.db")
        self.progress = ProgressTracker(db_path=db_path)

    def close(self) -> None:
        self.progress.close()

    def create_or_get_translation(
        self,
        episode: dict,
        target_lang: str = "zh-CN",
        voice_clone: bool = True,
        skip_steps: list[str] | None = None,
        force: bool = False,
    ) -> tuple[dict, bool]:
        audio_url = episode.get("audio_url", "")
        rss_url = episode.get("rss_url", "")
        rss_episode_id = episode.get("episode_id", "")
        if not audio_url:
            raise ValueError("episode audio_url is required")

        if not force:
            existing = self.progress.find_active_translation(
                audio_url=audio_url,
                rss_url=rss_url,
                rss_episode_id=rss_episode_id,
            )
            if existing:
                return row_to_job(existing), False

        episode_key = f"{rss_url}#{rss_episode_id}" if rss_url and rss_episode_id else audio_url
        episode_id = self.progress.get_or_create_episode(
            audio_url=audio_url,
            podcast_name=episode.get("podcast_name", "") or episode.get("feed_title", "") or "podcast",
            episode_title=episode.get("title", "episode"),
            episode_key=episode_key,
        )
        if force:
            self.progress.reset_episode(episode_id)

        job_id = make_job_id(episode_key)
        work_dir = os.path.abspath(os.path.join(
            self.config.get("output", {}).get("tasks_dir", "./output/translation_tasks"),
            job_id,
        ))
        os.makedirs(work_dir, exist_ok=True)
        log_path = os.path.join(work_dir, "run.log")
        artifacts = {"work_dir": work_dir, "log": log_path}
        estimated = self.config.get("podcast_tool", {}).get("estimated_minutes", 60)
        self.progress.mark_translation_queued(
            episode_id=episode_id,
            job_id=job_id,
            rss_url=rss_url,
            rss_episode_id=rss_episode_id,
            page_url=episode.get("page_url", ""),
            published_at=episode.get("published_at", ""),
            target_lang=target_lang,
            voice_clone=voice_clone,
            skip_steps=skip_steps or [],
            work_dir=work_dir,
            log_path=log_path,
            estimated_minutes=estimated,
            artifacts=artifacts,
        )
        row = self.progress.get_translation_by_job_id(job_id)
        return row_to_job(row), True

    def get(self, job_id: str) -> dict | None:
        row = self.progress.get_translation_by_job_id(job_id)
        return row_to_job(row) if row else None

    def get_row(self, job_id: str):
        return self.progress.get_translation_by_job_id(job_id)

    def list(self, status_filter: str = "active") -> list[dict]:
        return [row_to_job(row) for row in self.progress.list_translations(status_filter)]

    def update(
        self,
        job_id: str,
        status: str | None = None,
        stage: str | None = None,
        progress: float | None = None,
        message: str | None = None,
        pid: int | None = None,
        artifacts: dict | None = None,
        error: dict | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        self.progress.update_translation_state(
            job_id=job_id,
            status=status,
            stage=stage,
            progress=progress,
            message=message,
            pid=pid,
            artifacts=artifacts,
            error=error,
            started_at=now() if started else None,
            finished_at=now() if finished else None,
        )

    def cancel(self, job_id: str) -> dict | None:
        row = self.progress.get_translation_by_job_id(job_id)
        if not row:
            return None
        if row["status"] not in TERMINAL_STATUSES:
            self.update(
                job_id,
                status="cancelled",
                stage="cancelled",
                message="Translation cancelled.",
                finished=True,
            )
        row = self.progress.get_translation_by_job_id(job_id)
        return row_to_job(row)


def row_to_job(row, compact: bool = False) -> dict:
    if not row:
        return {}
    artifacts = parse_json(row["artifacts_json"], {})
    error = parse_json(row["error_json"], None)
    episode = {
        "rss_url": row["rss_url"] or "",
        "episode_id": row["rss_episode_id"] or row["episode_id"],
        "title": row["episode_title"] or "",
        "audio_url": row["audio_url"] or "",
        "page_url": row["page_url"] or "",
        "published_at": row["published_at"] or "",
    }
    job = {
        "job_id": row["job_id"],
        "status": row["status"],
        "stage": row["stage"] or row["status"],
        "progress": float(row["progress"] or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "estimated_minutes": row["estimated_minutes"],
        "message": row["message"] or "",
        "episode": episode,
        "artifacts": artifacts,
    }
    if not compact:
        job["started_at"] = row["started_at"]
        job["finished_at"] = row["finished_at"]
        job["pid"] = row["pid"]
    if error:
        job["error"] = error
    return job


def parse_json(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def make_job_id(key: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:6]
    return f"podcast_{stamp}_{digest}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()
