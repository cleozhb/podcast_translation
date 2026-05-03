import os

from core.app_factory import create_providers, create_shownote_llm, load_config
from core.pipeline import Pipeline
from core.progress import ProgressTracker

from podcast_tool import rss
from podcast_tool.state import TranslationStore, parse_json


class TranslationCancelled(Exception):
    pass


def run_translation(job_id: str, config_path: str = "config.yaml") -> int:
    config = load_config(config_path, quiet=True)
    store = TranslationStore(config, config_path=config_path)
    job = store.get(job_id)
    if not job:
        store.close()
        return 3
    if job["status"] == "cancelled":
        store.close()
        return 0

    store.update(
        job_id,
        status="running",
        stage="download",
        progress=max(job.get("progress", 0), 0.0),
        message="Translation worker started.",
        started=True,
    )

    row = store.get_row(job_id)
    skip_steps = parse_json(row["skip_steps_json"], [])
    artifacts = parse_json(row["artifacts_json"], {})
    work_dir = row["work_dir"] or artifacts.get("work_dir", "")
    if work_dir:
        os.makedirs(work_dir, exist_ok=True)

    progress = None
    try:
        stt, llm, tts, storage = create_providers(config)
        shownote_llm = None
        if "shownote" not in skip_steps:
            shownote_llm = create_shownote_llm(config)

        db_path = config.get("output", {}).get("progress_db", "./data/progress.db")
        progress = ProgressTracker(db_path=db_path)

        episode = job["episode"]
        rss_entry = None
        if episode.get("rss_url") and row["rss_episode_id"]:
            try:
                _, rss_entry = rss.find_episode(episode["rss_url"], row["rss_episode_id"], config)
            except Exception as exc:
                print(f"  ⚠️ 重新获取 RSS entry 失败，Shownote 将不含原始 RSS 信息: {exc}")

        local_audio_path = ""
        audio_url = episode.get("audio_url", "")
        if audio_url.startswith("local://"):
            local_audio_path = audio_url.removeprefix("local://")

        def status_callback(stage: str, progress: float, message: str, artifacts: dict | None = None):
            current = store.get(job_id)
            if current and current["status"] == "cancelled":
                raise TranslationCancelled("Translation cancelled.")
            merged_artifacts = dict(parse_json(store.get_row(job_id)["artifacts_json"], {}))
            merged_artifacts.update(artifacts or {})
            if work_dir:
                merged_artifacts["work_dir"] = work_dir
            if row["log_path"]:
                merged_artifacts["log"] = row["log_path"]
            store.update(
                job_id,
                status="running",
                stage=stage,
                progress=progress,
                message=message,
                artifacts=merged_artifacts,
            )

        pipeline = Pipeline(
            config,
            stt,
            llm,
            tts,
            storage,
            progress=progress,
            shownote_llm=shownote_llm,
            interactive_review=False,
            status_callback=status_callback,
        )
        ctx = pipeline.run(
            audio_url=audio_url,
            podcast_name=episode.get("rss_url") or "podcast",
            episode_title=episode.get("title", "episode"),
            skip_steps=skip_steps,
            local_audio_path=local_audio_path,
            rss_entry=rss_entry,
        )

        if store.get(job_id)["status"] == "cancelled":
            return 0

        final_artifacts = dict(parse_json(store.get_row(job_id)["artifacts_json"], {}))
        final_artifacts.update(Pipeline._artifacts_from_context(ctx))
        if work_dir:
            final_artifacts["work_dir"] = work_dir
        if row["log_path"]:
            final_artifacts["log"] = row["log_path"]

        if ctx.errors:
            error = {
                "code": "PIPELINE_ERROR",
                "message": "; ".join(ctx.errors),
                "retryable": True,
            }
            store.update(
                job_id,
                status="failed",
                stage="failed",
                message=error["message"],
                artifacts=final_artifacts,
                error=error,
                finished=True,
            )
            return 5

        store.update(
            job_id,
            status="completed",
            stage="done",
            progress=1.0,
            message="Translation completed.",
            artifacts=final_artifacts,
            finished=True,
        )
        return 0

    except TranslationCancelled:
        store.update(
            job_id,
            status="cancelled",
            stage="cancelled",
            message="Translation cancelled.",
            finished=True,
        )
        return 0
    except Exception as exc:
        error = classify_error(exc)
        store.update(
            job_id,
            status="failed",
            stage="failed",
            message=error["message"],
            error=error,
            finished=True,
        )
        print(f"  ❌ 后台翻译任务失败: {type(exc).__name__}: {exc}")
        return 5
    finally:
        if progress:
            progress.close()
        store.close()


def classify_error(exc: Exception) -> dict:
    text = str(exc)
    code = "PIPELINE_ERROR"
    retryable = True
    if "config" in text.lower() or "api_key" in text.lower():
        code = "CONFIG_ERROR"
        retryable = False
    elif "ffmpeg" in text.lower():
        code = "LOCAL_DEPENDENCY_ERROR"
        retryable = False
    return {"code": code, "message": text, "retryable": retryable}
