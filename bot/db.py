"""
データベース層 - SQLite を使用した Discord ID / GitHub ID マッピング管理
本番環境では PostgreSQL や PlanetScale 等に差し替え可能
"""

import sqlite3
import os
from pathlib import Path

DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).parent / "data" / "bot.db"))


class Database:
    def __init__(self):
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_links (
                    discord_id      TEXT PRIMARY KEY,
                    github_username TEXT NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    # ─── 書き込み ───────────────────────────────────────────────

    def save_link(self, discord_id: str, github_username: str):
        """Discord ID と GitHub ユーザー名を紐付けて保存（upsert）"""
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO user_links (discord_id, github_username, status, updated_at)
                VALUES (?, ?, 'pending', CURRENT_TIMESTAMP)
                ON CONFLICT(discord_id) DO UPDATE SET
                    github_username = excluded.github_username,
                    status          = 'pending',
                    updated_at      = CURRENT_TIMESTAMP
            """, (discord_id, github_username))
            conn.commit()

    def set_status(self, discord_id: str, status: str):
        """ステータスを更新する: pending / approved / rejected"""
        with self._connect() as conn:
            conn.execute("""
                UPDATE user_links
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE discord_id = ?
            """, (status, discord_id))
            conn.commit()

    # ─── 読み取り ───────────────────────────────────────────────

    def get_github_id(self, discord_id: str) -> str | None:
        """Discord ID から GitHub ユーザー名を取得"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT github_username FROM user_links WHERE discord_id = ?",
                (discord_id,),
            ).fetchone()
            return row["github_username"] if row else None

    def get_status(self, discord_id: str) -> str | None:
        """申請ステータスを取得"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM user_links WHERE discord_id = ?",
                (discord_id,),
            ).fetchone()
            return row["status"] if row else None

    def get_all_pending(self) -> list[str]:
        """承認待ちの Discord ID 一覧（Persistent View 復元用）"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT discord_id FROM user_links WHERE status = 'pending'"
            ).fetchall()
            return [row["discord_id"] for row in rows]

    def get_by_github(self, github_username: str) -> dict | None:
        """GitHub ユーザー名からレコードを取得"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_links WHERE github_username = ?",
                (github_username,),
            ).fetchone()
            return dict(row) if row else None
