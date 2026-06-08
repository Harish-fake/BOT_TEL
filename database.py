import sqlite3
import os
import threading
from datetime import datetime
from typing import Optional
from config import config


class Database:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls) -> "Database":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        os.makedirs(os.path.dirname(config.DATABASE_PATH), exist_ok=True)
        self.conn: sqlite3.Connection = sqlite3.connect(
            config.DATABASE_PATH, check_same_thread=False
        )
        self.conn.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS github_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                account_alias TEXT NOT NULL,
                github_username TEXT NOT NULL,
                token_encrypted TEXT NOT NULL,
                default_repo TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                github_account_id INTEGER REFERENCES github_accounts(id) ON DELETE SET NULL,
                project_name TEXT NOT NULL,
                project_path TEXT NOT NULL,
                github_repo TEXT,
                schedule_time TEXT,
                last_push TIMESTAMP,
                batch_size INTEGER DEFAULT 4,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                cron_expression TEXT NOT NULL,
                timezone TEXT DEFAULT 'UTC',
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sync_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                files_changed INTEGER DEFAULT 0,
                commit_hash TEXT,
                duration_ms INTEGER,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS pushed_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                file_path TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                pushed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(project_id, file_path)
            );
        """
        )
        try:
            self.conn.execute("ALTER TABLE projects ADD COLUMN batch_size INTEGER DEFAULT 4")
        except Exception:
            pass
        self.conn.commit()

    # ── Users ──────────────────────────────────────────────

    def upsert_user(
        self, telegram_id: int, username: Optional[str], first_name: Optional[str]
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO users (telegram_id, username, first_name)
               VALUES (?, ?, ?)
               ON CONFLICT(telegram_id) DO UPDATE SET
                   username = COALESCE(excluded.username, users.username),
                   first_name = COALESCE(excluded.first_name, users.first_name)
               RETURNING id""",
            (telegram_id, username, first_name),
        )
        row = cur.fetchone()
        self.conn.commit()
        return row["id"] if row else 0

    def get_user_by_telegram_id(self, telegram_id: int) -> Optional[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        return cur.fetchone()

    def get_user(self, user_id: int) -> Optional[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return cur.fetchone()

    def get_all_users(self) -> list[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM users ORDER BY created_at DESC")
        return cur.fetchall()

    def get_total_users(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) as cnt FROM users")
        return cur.fetchone()["cnt"]

    # ── GitHub Accounts ────────────────────────────────────

    def add_github_account(
        self,
        user_id: int,
        account_alias: str,
        github_username: str,
        token_encrypted: str,
        default_repo: Optional[str] = None,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO github_accounts (user_id, account_alias, github_username, token_encrypted, default_repo)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, account_alias, github_username, token_encrypted, default_repo),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_github_accounts(self, user_id: int) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM github_accounts WHERE user_id = ?", (user_id,)
        )
        return cur.fetchall()

    def get_github_account(self, account_id: int) -> Optional[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM github_accounts WHERE id = ?", (account_id,)
        )
        return cur.fetchone()

    def delete_github_account(self, account_id: int, user_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM github_accounts WHERE id = ? AND user_id = ?",
            (account_id, user_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ── Projects ───────────────────────────────────────────

    def add_project(
        self,
        user_id: int,
        project_name: str,
        project_path: str,
        github_account_id: Optional[int] = None,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO projects (user_id, project_name, project_path, github_account_id)
               VALUES (?, ?, ?, ?)""",
            (user_id, project_name, project_path, github_account_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def delete_project(self, project_id: int) -> None:
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("DELETE FROM pushed_files WHERE project_id = ?", (project_id,))
        self.conn.execute("DELETE FROM schedules WHERE project_id = ?", (project_id,))
        self.conn.execute("DELETE FROM sync_logs WHERE project_id = ?", (project_id,))
        self.conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        self.conn.commit()

    def get_project(self, project_id: int) -> Optional[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        return cur.fetchone()

    def get_user_projects(self, user_id: int) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM projects WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        return cur.fetchall()

    def get_all_projects(self) -> list[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM projects ORDER BY created_at DESC")
        return cur.fetchall()

    def set_project_github(
        self,
        project_id: int,
        github_account_id: int,
        github_repo: str,
    ) -> None:
        self.conn.execute(
            """UPDATE projects
               SET github_account_id = ?, github_repo = ?
               WHERE id = ?""",
            (github_account_id, github_repo, project_id),
        )
        self.conn.commit()

    def set_project_schedule(self, project_id: int, schedule_time: str) -> None:
        self.conn.execute(
            "UPDATE projects SET schedule_time = ? WHERE id = ?",
            (schedule_time, project_id),
        )
        self.conn.commit()

    def get_batch_size(self, project_id: int) -> int:
        cur = self.conn.execute(
            "SELECT batch_size FROM projects WHERE id = ?", (project_id,)
        )
        row = cur.fetchone()
        return row["batch_size"] if row and row["batch_size"] else 4

    def set_batch_size(self, project_id: int, batch_size: int) -> None:
        self.conn.execute(
            "UPDATE projects SET batch_size = ? WHERE id = ?",
            (batch_size, project_id),
        )
        self.conn.commit()

    def set_project_last_push(self, project_id: int) -> None:
        self.conn.execute(
            "UPDATE projects SET last_push = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), project_id),
        )
        self.conn.commit()

    def get_total_projects(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) as cnt FROM projects")
        return cur.fetchone()["cnt"]

    # ── Schedules ──────────────────────────────────────────

    def add_schedule(
        self, project_id: int, cron_expression: str, timezone: str = "UTC"
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO schedules (project_id, cron_expression, timezone)
               VALUES (?, ?, ?)""",
            (project_id, cron_expression, timezone),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_schedule(
        self,
        schedule_id: int,
        cron_expression: str,
        timezone: str = "UTC",
    ) -> None:
        self.conn.execute(
            "UPDATE schedules SET cron_expression = ?, timezone = ? WHERE id = ?",
            (cron_expression, timezone, schedule_id),
        )
        self.conn.commit()

    def get_schedule_by_project(self, project_id: int) -> Optional[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM schedules WHERE project_id = ?", (project_id,)
        )
        return cur.fetchone()

    def get_all_enabled_schedules(self) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT s.* FROM schedules s WHERE s.enabled = 1"
        )
        return cur.fetchall()

    def enable_schedule(self, schedule_id: int, enabled: bool) -> None:
        self.conn.execute(
            "UPDATE schedules SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, schedule_id),
        )
        self.conn.commit()

    def delete_schedule(self, schedule_id: int) -> None:
        self.conn.execute(
            "DELETE FROM schedules WHERE id = ?", (schedule_id,)
        )
        self.conn.commit()

    # ── Sync Logs ──────────────────────────────────────────

    def add_sync_log(
        self,
        project_id: int,
        status: str,
        files_changed: int = 0,
        commit_hash: Optional[str] = None,
        duration_ms: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO sync_logs
               (project_id, status, files_changed, commit_hash, duration_ms, error_message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, status, files_changed, commit_hash, duration_ms, error_message),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_sync_logs(self, project_id: int, limit: int = 10) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM sync_logs WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        )
        return cur.fetchall()

    def get_total_syncs(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) as cnt FROM sync_logs")
        return cur.fetchone()["cnt"]

    def get_failed_syncs(self) -> int:
        cur = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM sync_logs WHERE status = 'failure'"
        )
        return cur.fetchone()["cnt"]

    # ── Settings ───────────────────────────────────────────

    def get_setting(self, key: str) -> Optional[str]:
        cur = self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    # ── Pushed Files ────────────────────────────────────

    def record_pushed_file(self, project_id: int, file_path: str, file_hash: str) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO pushed_files (project_id, file_path, file_hash)
               VALUES (?, ?, ?)""",
            (project_id, file_path, file_hash),
        )
        self.conn.commit()

    def get_pushed_files(self, project_id: int) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM pushed_files WHERE project_id = ?", (project_id,)
        )
        return cur.fetchall()

    def get_pushed_file_hashes(self, project_id: int) -> dict[str, str]:
        cur = self.conn.execute(
            "SELECT file_path, file_hash FROM pushed_files WHERE project_id = ?",
            (project_id,),
        )
        return {row["file_path"]: row["file_hash"] for row in cur.fetchall()}

    def count_pushed_files(self, project_id: int) -> int:
        cur = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM pushed_files WHERE project_id = ?",
            (project_id,),
        )
        return cur.fetchone()["cnt"]

    def delete_pushed_file(self, project_id: int, file_path: str) -> None:
        self.conn.execute(
            "DELETE FROM pushed_files WHERE project_id = ? AND file_path = ?",
            (project_id, file_path),
        )
        self.conn.commit()

    def clear_pushed_files(self, project_id: int) -> None:
        self.conn.execute(
            "DELETE FROM pushed_files WHERE project_id = ?", (project_id,)
        )
        self.conn.commit()

    # ── Stats ──────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "total_users": self.get_total_users(),
            "total_projects": self.get_total_projects(),
            "total_syncs": self.get_total_syncs(),
            "failed_syncs": self.get_failed_syncs(),
        }

    def close(self) -> None:
        self.conn.close()


db = Database()
