"""SQLite 进度追踪：记录每个 episode 的处理状态，支持断点续跑。"""

import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger


class ProgressTracker:
    """
    用 SQLite 追踪每个 episode 的处理状态。

    状态流转: pending → downloading → transcribing → translating → synthesizing → completed
    任何步骤失败: → failed
    """

    STATUSES = (
        "pending",
        "downloading",
        "transcribing",
        "translating",
        "synthesizing",
        "completed",
        "failed",
    )

    def __init__(self, db_path: str = "data/progress.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self._init_db()

    def _init_db(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                guid TEXT PRIMARY KEY,
                podcast_name TEXT,
                title TEXT,
                status TEXT DEFAULT 'pending',
                current_step TEXT,
                audio_path TEXT,
                transcript_path TEXT,
                translation_path TEXT,
                output_audio_path TEXT,
                error_message TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        self.conn.commit()

    def is_completed(self, guid: str) -> bool:
        """检查 episode 是否已处理完成。"""
        cursor = self.conn.execute(
            "SELECT status FROM episodes WHERE guid = ?", (guid,)
        )
        row = cursor.fetchone()
        return row is not None and row[0] == "completed"

    def get_status(self, guid: str) -> str | None:
        """获取 episode 当前状态。"""
        cursor = self.conn.execute(
            "SELECT status FROM episodes WHERE guid = ?", (guid,)
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def update_status(
        self, guid: str, status: str, podcast_name: str = "", title: str = "", **kwargs
    ):
        """更新 episode 的处理状态和相关路径。"""
        now = datetime.now().isoformat()

        # 检查是否已有记录
        existing = self.get_status(guid)
        if existing is None:
            self.conn.execute(
                """INSERT INTO episodes (guid, podcast_name, title, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (guid, podcast_name, title, status, now, now),
            )
        else:
            self.conn.execute(
                "UPDATE episodes SET status = ?, updated_at = ? WHERE guid = ?",
                (status, now, guid),
            )

        # 更新额外字段
        for key, value in kwargs.items():
            if key in (
                "audio_path",
                "transcript_path",
                "translation_path",
                "output_audio_path",
                "error_message",
                "current_step",
            ):
                self.conn.execute(
                    f"UPDATE episodes SET {key} = ? WHERE guid = ?",
                    (str(value), guid),
                )

        self.conn.commit()
        logger.debug(f"进度更新: {guid[:16]}... → {status}")

    def mark_failed(self, guid: str, error: str):
        """标记 episode 为失败。"""
        self.update_status(guid, "failed", error_message=error)

    def list_all(self) -> list[dict]:
        """列出所有 episode 的状态。"""
        cursor = self.conn.execute(
            "SELECT guid, podcast_name, title, status, updated_at FROM episodes ORDER BY updated_at DESC"
        )
        return [
            {
                "guid": row[0],
                "podcast_name": row[1],
                "title": row[2],
                "status": row[3],
                "updated_at": row[4],
            }
            for row in cursor.fetchall()
        ]

    def close(self):
        self.conn.close()
