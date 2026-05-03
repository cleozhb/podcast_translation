"""
core/progress.py
================
SQLite 进度追踪：记录每个 episode 各步骤的完成状态，支持断点续跑。
"""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any


class ProgressTracker:
    """
    用 SQLite 跟踪 pipeline 每步的完成状态。

    用法:
        tracker = ProgressTracker("./data/progress.db")
        eid = tracker.get_or_create_episode(audio_url, name, title)
        completed = tracker.get_completed_steps(eid)
        # ... run steps ...
        tracker.mark_step_completed(eid, "download", {"local_audio_path": "..."})
        tracker.close()
    """

    STEPS = ["download", "voiceprint", "stt", "translate", "tts", "shownote"]
    ACTIVE_STATUSES = ("queued", "running")
    TERMINAL_STATUSES = ("completed", "failed", "cancelled")
    JSON_FIELDS = {"skip_steps_json", "artifacts_json", "error_json"}

    def __init__(self, db_path: str = "./data/progress.db"):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS episodes (
                episode_id    TEXT PRIMARY KEY,
                audio_url     TEXT NOT NULL UNIQUE,
                podcast_name  TEXT DEFAULT '',
                episode_title TEXT DEFAULT '',
                status        TEXT DEFAULT 'pending',
                error_message TEXT,
                created_at    TEXT,
                updated_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS step_results (
                episode_id    TEXT NOT NULL,
                step_name     TEXT NOT NULL,
                status        TEXT DEFAULT 'pending',
                result_data   TEXT,
                error_message TEXT,
                completed_at  TEXT,
                PRIMARY KEY (episode_id, step_name),
                FOREIGN KEY (episode_id) REFERENCES episodes(episode_id)
            );
        """)
        self._migrate_episode_columns()
        cur.executescript("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_episodes_job_id
            ON episodes(job_id)
            WHERE job_id IS NOT NULL AND job_id != '';

            CREATE INDEX IF NOT EXISTS idx_episodes_status
            ON episodes(status);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_episodes_rss_episode_active
            ON episodes(rss_url, rss_episode_id)
            WHERE status IN ('queued', 'running')
              AND rss_url != ''
              AND rss_episode_id != '';
        """)
        self.conn.commit()

    def _migrate_episode_columns(self) -> None:
        columns = {
            "job_id": "TEXT",
            "rss_url": "TEXT DEFAULT ''",
            "rss_episode_id": "TEXT DEFAULT ''",
            "page_url": "TEXT DEFAULT ''",
            "published_at": "TEXT DEFAULT ''",
            "stage": "TEXT DEFAULT 'pending'",
            "progress": "REAL DEFAULT 0",
            "message": "TEXT DEFAULT ''",
            "estimated_minutes": "INTEGER",
            "pid": "INTEGER",
            "started_at": "TEXT",
            "finished_at": "TEXT",
            "target_lang": "TEXT DEFAULT 'zh-CN'",
            "voice_clone": "INTEGER DEFAULT 1",
            "skip_steps_json": "TEXT DEFAULT '[]'",
            "work_dir": "TEXT DEFAULT ''",
            "log_path": "TEXT DEFAULT ''",
            "artifacts_json": "TEXT DEFAULT '{}'",
            "error_json": "TEXT",
        }
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(episodes)")
        existing = {row["name"] for row in cur.fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                cur.execute(f"ALTER TABLE episodes ADD COLUMN {name} {definition}")

    # ----------------------------------------------------------
    # Episode 操作
    # ----------------------------------------------------------

    @staticmethod
    def _make_episode_id(audio_url: str) -> str:
        return hashlib.sha256(audio_url.encode()).hexdigest()[:16]

    @classmethod
    def make_episode_id(cls, key: str) -> str:
        return cls._make_episode_id(key)

    def get_or_create_episode(
        self,
        audio_url: str,
        podcast_name: str = "",
        episode_title: str = "",
        episode_key: str = None,
    ) -> str:
        episode_id = self._make_episode_id(episode_key or audio_url)
        now = _now()
        cur = self.conn.cursor()
        cur.execute(
            "SELECT episode_id FROM episodes WHERE episode_id = ? OR audio_url = ? LIMIT 1",
            (episode_id, audio_url),
        )
        row = cur.fetchone()
        if row:
            existing_id = row["episode_id"]
            cur.execute(
                """UPDATE episodes
                   SET podcast_name = COALESCE(NULLIF(?, ''), podcast_name),
                       episode_title = COALESCE(NULLIF(?, ''), episode_title),
                       updated_at = ?
                   WHERE episode_id = ?""",
                (podcast_name, episode_title, now, existing_id),
            )
            self.conn.commit()
            return existing_id

        cur.execute(
            """INSERT INTO episodes (episode_id, audio_url, podcast_name, episode_title, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(episode_id) DO UPDATE SET updated_at = ?""",
            (episode_id, audio_url, podcast_name, episode_title, now, now, now),
        )
        self.conn.commit()
        return episode_id

    def get_completed_steps(self, episode_id: str) -> dict[str, dict]:
        """返回 {step_name: result_data_dict}，仅包含 status='completed' 的步骤。"""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT step_name, result_data FROM step_results WHERE episode_id = ? AND status = 'completed'",
            (episode_id,),
        )
        result = {}
        for row in cur.fetchall():
            data = json.loads(row["result_data"]) if row["result_data"] else {}
            result[row["step_name"]] = data
        return result

    def mark_episode_completed(self, episode_id: str) -> None:
        self._update_episode_status(
            episode_id, "completed", stage="done", progress=1.0, finished_at=_now()
        )

    def mark_episode_failed(self, episode_id: str, error: str) -> None:
        self._update_episode_status(episode_id, "failed", error, finished_at=_now())

    def reset_episode(self, episode_id: str) -> None:
        """清除该 episode 的所有步骤记录（用于 --no-resume 强制重跑）。"""
        cur = self.conn.cursor()
        cur.execute("DELETE FROM step_results WHERE episode_id = ?", (episode_id,))
        cur.execute(
            """UPDATE episodes
               SET status = 'pending',
                   stage = 'pending',
                   progress = 0,
                   message = '',
                   error_message = NULL,
                   error_json = NULL,
                   artifacts_json = '{}',
                   finished_at = NULL,
                   updated_at = ?
               WHERE episode_id = ?""",
            (_now(), episode_id),
        )
        self.conn.commit()

    def _update_episode_status(
        self,
        episode_id: str,
        status: str,
        error: str = None,
        stage: str = None,
        progress: float = None,
        message: str = None,
        finished_at: str = None,
    ) -> None:
        updates = {"status": status, "error_message": error, "updated_at": _now()}
        if stage is not None:
            updates["stage"] = stage
        if progress is not None:
            updates["progress"] = progress
        if message is not None:
            updates["message"] = message
        if finished_at is not None:
            updates["finished_at"] = finished_at
        self.update_episode(episode_id, **updates)

    # ----------------------------------------------------------
    # Translation task state
    # ----------------------------------------------------------

    def update_episode(self, episode_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields.setdefault("updated_at", _now())
        allowed = self._episode_columns()
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"未知 episode 字段: {', '.join(sorted(unknown))}")

        names = []
        values = []
        for key, value in fields.items():
            names.append(f"{key} = ?")
            if key in self.JSON_FIELDS and value is not None and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            values.append(value)
        values.append(episode_id)

        cur = self.conn.cursor()
        cur.execute(
            f"UPDATE episodes SET {', '.join(names)} WHERE episode_id = ?",
            values,
        )
        self.conn.commit()

    def mark_translation_queued(
        self,
        episode_id: str,
        job_id: str,
        rss_url: str = "",
        rss_episode_id: str = "",
        page_url: str = "",
        published_at: str = "",
        target_lang: str = "zh-CN",
        voice_clone: bool = True,
        skip_steps: list[str] = None,
        work_dir: str = "",
        log_path: str = "",
        estimated_minutes: int = None,
        artifacts: dict = None,
    ) -> None:
        self.update_episode(
            episode_id,
            job_id=job_id,
            rss_url=rss_url or "",
            rss_episode_id=rss_episode_id or "",
            page_url=page_url or "",
            published_at=published_at or "",
            status="queued",
            stage="queued",
            progress=0.0,
            message="Translation queued.",
            estimated_minutes=estimated_minutes,
            target_lang=target_lang,
            voice_clone=1 if voice_clone else 0,
            skip_steps_json=skip_steps or [],
            work_dir=work_dir,
            log_path=log_path,
            artifacts_json=artifacts or {},
            error_json=None,
            started_at=None,
            finished_at=None,
            pid=None,
        )

    def update_translation_state(
        self,
        job_id: str,
        status: str = None,
        stage: str = None,
        progress: float = None,
        message: str = None,
        pid: int = None,
        artifacts: dict = None,
        error: dict = None,
        started_at: str = None,
        finished_at: str = None,
    ) -> None:
        fields: dict[str, Any] = {}
        if status is not None:
            fields["status"] = status
        if stage is not None:
            fields["stage"] = stage
        if progress is not None:
            fields["progress"] = progress
        if message is not None:
            fields["message"] = message
        if pid is not None:
            fields["pid"] = pid
        if artifacts is not None:
            fields["artifacts_json"] = artifacts
        if error is not None:
            fields["error_json"] = error
            fields["error_message"] = error.get("message") if isinstance(error, dict) else str(error)
        if started_at is not None:
            fields["started_at"] = started_at
        if finished_at is not None:
            fields["finished_at"] = finished_at
        fields["updated_at"] = _now()

        cur = self.conn.cursor()
        names = []
        values = []
        for key, value in fields.items():
            names.append(f"{key} = ?")
            if key in self.JSON_FIELDS and value is not None and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            values.append(value)
        values.append(job_id)
        cur.execute(
            f"UPDATE episodes SET {', '.join(names)} WHERE job_id = ?",
            values,
        )
        self.conn.commit()

    def get_episode(self, episode_id: str) -> sqlite3.Row | None:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM episodes WHERE episode_id = ?", (episode_id,))
        return cur.fetchone()

    def get_translation_by_job_id(self, job_id: str) -> sqlite3.Row | None:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM episodes WHERE job_id = ?", (job_id,))
        return cur.fetchone()

    def find_active_translation(
        self,
        audio_url: str = "",
        rss_url: str = "",
        rss_episode_id: str = "",
    ) -> sqlite3.Row | None:
        cur = self.conn.cursor()
        clauses = ["status IN ('queued', 'running')"]
        params: list[Any] = []
        match_clauses = []
        if rss_url and rss_episode_id:
            match_clauses.append("(rss_url = ? AND rss_episode_id = ?)")
            params.extend([rss_url, rss_episode_id])
        if audio_url:
            match_clauses.append("audio_url = ?")
            params.append(audio_url)
        if not match_clauses:
            return None
        clauses.append("(" + " OR ".join(match_clauses) + ")")
        cur.execute(
            f"SELECT * FROM episodes WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT 1",
            params,
        )
        return cur.fetchone()

    def list_translations(self, status_filter: str = "active") -> list[sqlite3.Row]:
        cur = self.conn.cursor()
        if status_filter == "active":
            cur.execute(
                "SELECT * FROM episodes WHERE status IN ('queued', 'running') AND job_id != '' ORDER BY updated_at DESC"
            )
        elif status_filter == "all":
            cur.execute("SELECT * FROM episodes WHERE job_id != '' ORDER BY updated_at DESC")
        else:
            cur.execute(
                "SELECT * FROM episodes WHERE status = ? AND job_id != '' ORDER BY updated_at DESC",
                (status_filter,),
            )
        return list(cur.fetchall())

    def _episode_columns(self) -> set[str]:
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(episodes)")
        return {row["name"] for row in cur.fetchall()}

    # ----------------------------------------------------------
    # Step 操作
    # ----------------------------------------------------------

    def mark_step_completed(
        self, episode_id: str, step_name: str, result_data: dict
    ) -> None:
        self._upsert_step(episode_id, step_name, "completed", result_data)

    def mark_step_skipped(self, episode_id: str, step_name: str) -> None:
        self._upsert_step(episode_id, step_name, "skipped")

    def mark_step_failed(
        self, episode_id: str, step_name: str, error: str
    ) -> None:
        self._upsert_step(episode_id, step_name, "failed", error_message=error)

    def _upsert_step(
        self,
        episode_id: str,
        step_name: str,
        status: str,
        result_data: dict = None,
        error_message: str = None,
    ) -> None:
        now = _now()
        data_json = json.dumps(result_data, ensure_ascii=False) if result_data else None
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO step_results (episode_id, step_name, status, result_data, error_message, completed_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(episode_id, step_name) DO UPDATE
                   SET status = ?, result_data = COALESCE(?, result_data), error_message = ?, completed_at = ?""",
            (
                episode_id, step_name, status, data_json, error_message, now,
                status, data_json, error_message, now,
            ),
        )
        self.conn.commit()

    # ----------------------------------------------------------

    def close(self) -> None:
        self.conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
