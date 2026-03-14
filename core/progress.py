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

    STEPS = ["download", "voiceprint", "stt", "translate", "tts"]

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
        self.conn.commit()

    # ----------------------------------------------------------
    # Episode 操作
    # ----------------------------------------------------------

    @staticmethod
    def _make_episode_id(audio_url: str) -> str:
        return hashlib.sha256(audio_url.encode()).hexdigest()[:16]

    def get_or_create_episode(
        self, audio_url: str, podcast_name: str = "", episode_title: str = ""
    ) -> str:
        episode_id = self._make_episode_id(audio_url)
        now = _now()
        cur = self.conn.cursor()
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
        self._update_episode_status(episode_id, "completed")

    def mark_episode_failed(self, episode_id: str, error: str) -> None:
        self._update_episode_status(episode_id, "failed", error)

    def reset_episode(self, episode_id: str) -> None:
        """清除该 episode 的所有步骤记录（用于 --no-resume 强制重跑）。"""
        cur = self.conn.cursor()
        cur.execute("DELETE FROM step_results WHERE episode_id = ?", (episode_id,))
        cur.execute(
            "UPDATE episodes SET status = 'pending', error_message = NULL, updated_at = ? WHERE episode_id = ?",
            (_now(), episode_id),
        )
        self.conn.commit()

    def _update_episode_status(
        self, episode_id: str, status: str, error: str = None
    ) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE episodes SET status = ?, error_message = ?, updated_at = ? WHERE episode_id = ?",
            (status, error, _now(), episode_id),
        )
        self.conn.commit()

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
