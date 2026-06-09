import os
import logging
import sqlite3
import threading
from datetime import datetime
from typing import Optional
from config import config

logger = logging.getLogger(__name__)


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

        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            self._init_pg(db_url)
        else:
            self._init_sqlite()

    # ── Backend init ────────────────────────────────────────

    def _init_sqlite(self) -> None:
        os.makedirs(os.path.dirname(config.DATABASE_PATH), exist_ok=True)
        self.conn: sqlite3.Connection = sqlite3.connect(
            config.DATABASE_PATH, check_same_thread=False
        )
        self.conn.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._pg = False
        self._init_schema()

    def _init_pg(self, db_url: str) -> None:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        if not db_url.startswith("postgres"):
            logger.warning(f"DATABASE_URL does not look like a PostgreSQL URL (starts with '{db_url[:20]}...'). Falling back to SQLite.")
            return self._init_sqlite()

        try:
            self.conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor, connect_timeout=10)
            self.conn.autocommit = False
            self._pg = True
            self._init_schema()
        except Exception as e:
            logger.warning(f"PostgreSQL connection failed: {e}. Falling back to SQLite.")
            self._init_sqlite()

    def _e(self, sql: str, params: tuple = ()) -> "CursorProxy":
        if self._pg:
            cur = self.conn.cursor()
            cur.execute(sql, params)
        else:
            cur = self.conn.execute(sql.replace("%s", "?"), params)
        return CursorProxy(cur, self._pg)

    def _init_schema(self) -> None:
        if self._pg:
            pk = "SERIAL PRIMARY KEY"
            now = "DEFAULT CURRENT_TIMESTAMP"
        else:
            pk = "INTEGER PRIMARY KEY AUTOINCREMENT"
            now = "DEFAULT CURRENT_TIMESTAMP"

        schema = f"""
            CREATE TABLE IF NOT EXISTS users (
                id {pk},
                telegram_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP {now}
            );
            CREATE TABLE IF NOT EXISTS github_accounts (
                id {pk},
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                account_alias TEXT NOT NULL,
                github_username TEXT NOT NULL,
                token_encrypted TEXT NOT NULL,
                default_repo TEXT,
                created_at TIMESTAMP {now}
            );
            CREATE TABLE IF NOT EXISTS projects (
                id {pk},
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                github_account_id INTEGER REFERENCES github_accounts(id) ON DELETE SET NULL,
                project_name TEXT NOT NULL,
                project_path TEXT NOT NULL,
                github_repo TEXT,
                schedule_time TEXT,
                last_push TIMESTAMP,
                batch_size INTEGER DEFAULT 4,
                created_at TIMESTAMP {now}
            );
            CREATE TABLE IF NOT EXISTS schedules (
                id {pk},
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                cron_expression TEXT NOT NULL,
                timezone TEXT DEFAULT 'UTC',
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP {now}
            );
            CREATE TABLE IF NOT EXISTS sync_logs (
                id {pk},
                project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                files_changed INTEGER DEFAULT 0,
                commit_hash TEXT,
                duration_ms INTEGER,
                error_message TEXT,
                created_at TIMESTAMP {now}
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS pushed_files (
                id {pk},
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                file_path TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                pushed_at TIMESTAMP {now},
                UNIQUE(project_id, file_path)
            );
        """
        if self._pg:
            self.conn.execute(schema)
        else:
            self.conn.executescript(schema)
        try:
            self.conn.execute("ALTER TABLE projects ADD COLUMN batch_size INTEGER DEFAULT 4")
        except Exception:
            pass
        self.conn.commit()

    # ── Users ──────────────────────────────────────────────

    def upsert_user(self, telegram_id: int, username: Optional[str], first_name: Optional[str]) -> int:
        sql = (
            "INSERT INTO users (telegram_id, username, first_name) VALUES (%s, %s, %s) "
            "ON CONFLICT(telegram_id) DO UPDATE SET "
            "username = COALESCE(EXCLUDED.username, users.username), "
            "first_name = COALESCE(EXCLUDED.first_name, users.first_name) "
            "RETURNING id"
        )
        row = self._e(sql, (telegram_id, username, first_name)).fetchone()
        self.conn.commit()
        return row["id"] if row else 0

    def get_user_by_telegram_id(self, telegram_id: int) -> Optional[dict]:
        return self._e("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,)).fetchone()

    def get_user(self, user_id: int) -> Optional[dict]:
        return self._e("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()

    def get_all_users(self) -> list[dict]:
        return self._e("SELECT * FROM users ORDER BY created_at DESC").fetchall()

    def get_total_users(self) -> int:
        return self._e("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]

    # ── GitHub Accounts ────────────────────────────────────

    def add_github_account(self, user_id: int, account_alias: str, github_username: str, token_encrypted: str, default_repo: Optional[str] = None) -> int:
        return self._e(
            "INSERT INTO github_accounts (user_id, account_alias, github_username, token_encrypted, default_repo) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (user_id, account_alias, github_username, token_encrypted, default_repo),
        ).fetchone()["id"]

    def get_github_accounts(self, user_id: int) -> list[dict]:
        return self._e("SELECT * FROM github_accounts WHERE user_id = %s", (user_id,)).fetchall()

    def get_github_account(self, account_id: int) -> Optional[dict]:
        return self._e("SELECT * FROM github_accounts WHERE id = %s", (account_id,)).fetchone()

    def delete_github_account(self, account_id: int, user_id: int) -> bool:
        r = self._e("DELETE FROM github_accounts WHERE id = %s AND user_id = %s", (account_id, user_id))
        self.conn.commit()
        return r.rowcount > 0

    # ── Projects ───────────────────────────────────────────

    def add_project(self, user_id: int, project_name: str, project_path: str, github_account_id: Optional[int] = None) -> int:
        return self._e(
            "INSERT INTO projects (user_id, project_name, project_path, github_account_id) VALUES (%s, %s, %s, %s) RETURNING id",
            (user_id, project_name, project_path, github_account_id),
        ).fetchone()["id"]

    def delete_project(self, project_id: int) -> None:
        for tbl in ("pushed_files", "schedules", "sync_logs", "projects"):
            self._e(f"DELETE FROM {tbl} WHERE project_id = %s", (project_id,))
        self.conn.commit()

    def get_project(self, project_id: int) -> Optional[dict]:
        return self._e("SELECT * FROM projects WHERE id = %s", (project_id,)).fetchone()

    def get_user_projects(self, user_id: int) -> list[dict]:
        return self._e("SELECT * FROM projects WHERE user_id = %s ORDER BY created_at DESC", (user_id,)).fetchall()

    def get_all_projects(self) -> list[dict]:
        return self._e("SELECT * FROM projects ORDER BY created_at DESC").fetchall()

    def set_project_github(self, project_id: int, github_account_id: int, github_repo: str) -> None:
        self._e("UPDATE projects SET github_account_id = %s, github_repo = %s WHERE id = %s", (github_account_id, github_repo, project_id))
        self.conn.commit()

    def set_project_schedule(self, project_id: int, schedule_time: str) -> None:
        self._e("UPDATE projects SET schedule_time = %s WHERE id = %s", (schedule_time, project_id))
        self.conn.commit()

    def get_batch_size(self, project_id: int) -> int:
        row = self._e("SELECT batch_size FROM projects WHERE id = %s", (project_id,)).fetchone()
        if row is None:
            return 4
        val = row["batch_size"]
        return val if val is not None else 4

    def set_batch_size(self, project_id: int, batch_size: int) -> None:
        self._e("UPDATE projects SET batch_size = %s WHERE id = %s", (batch_size, project_id))
        self.conn.commit()

    def set_project_last_push(self, project_id: int) -> None:
        self._e("UPDATE projects SET last_push = %s WHERE id = %s", (datetime.utcnow().isoformat(), project_id))
        self.conn.commit()

    def get_total_projects(self) -> int:
        return self._e("SELECT COUNT(*) as cnt FROM projects").fetchone()["cnt"]

    # ── Schedules ──────────────────────────────────────────

    def add_schedule(self, project_id: int, cron_expression: str, timezone: str = "Asia/Kolkata") -> int:
        return self._e(
            "INSERT INTO schedules (project_id, cron_expression, timezone) VALUES (%s, %s, %s) RETURNING id",
            (project_id, cron_expression, timezone),
        ).fetchone()["id"]

    def update_schedule(self, schedule_id: int, cron_expression: str, timezone: str = "UTC") -> None:
        self._e("UPDATE schedules SET cron_expression = %s, timezone = %s WHERE id = %s", (cron_expression, timezone, schedule_id))
        self.conn.commit()

    def get_schedule_by_project(self, project_id: int) -> Optional[dict]:
        return self._e("SELECT * FROM schedules WHERE project_id = %s", (project_id,)).fetchone()

    def get_all_enabled_schedules(self) -> list[dict]:
        return self._e("SELECT s.* FROM schedules s WHERE s.enabled = 1").fetchall()

    def enable_schedule(self, schedule_id: int, enabled: bool) -> None:
        self._e("UPDATE schedules SET enabled = %s WHERE id = %s", (1 if enabled else 0, schedule_id))
        self.conn.commit()

    def delete_schedule(self, schedule_id: int) -> None:
        self._e("DELETE FROM schedules WHERE id = %s", (schedule_id,))
        self.conn.commit()

    # ── Sync Logs ──────────────────────────────────────────

    def add_sync_log(self, project_id: int, status: str, files_changed: int = 0, commit_hash: Optional[str] = None, duration_ms: Optional[int] = None, error_message: Optional[str] = None) -> int:
        return self._e(
            "INSERT INTO sync_logs (project_id, status, files_changed, commit_hash, duration_ms, error_message) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (project_id, status, files_changed, commit_hash, duration_ms, error_message),
        ).fetchone()["id"]

    def get_sync_logs(self, project_id: int, limit: int = 10) -> list[dict]:
        return self._e("SELECT * FROM sync_logs WHERE project_id = %s ORDER BY created_at DESC LIMIT %s", (project_id, limit)).fetchall()

    def get_total_syncs(self) -> int:
        return self._e("SELECT COUNT(*) as cnt FROM sync_logs").fetchone()["cnt"]

    def get_failed_syncs(self) -> int:
        return self._e("SELECT COUNT(*) as cnt FROM sync_logs WHERE status = 'failure'").fetchone()["cnt"]

    # ── Settings ───────────────────────────────────────────

    def get_setting(self, key: str) -> Optional[str]:
        row = self._e("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self._e("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value", (key, value))
        self.conn.commit()

    # ── Pushed Files ────────────────────────────────────

    def record_pushed_file(self, project_id: int, file_path: str, file_hash: str) -> None:
        self._e(
            "INSERT INTO pushed_files (project_id, file_path, file_hash) VALUES (%s, %s, %s) ON CONFLICT(project_id, file_path) DO UPDATE SET file_hash = EXCLUDED.file_hash",
            (project_id, file_path, file_hash),
        )
        self.conn.commit()

    def get_pushed_files(self, project_id: int) -> list[dict]:
        return self._e("SELECT * FROM pushed_files WHERE project_id = %s", (project_id,)).fetchall()

    def get_pushed_file_hashes(self, project_id: int) -> dict[str, str]:
        rows = self._e("SELECT file_path, file_hash FROM pushed_files WHERE project_id = %s", (project_id,)).fetchall()
        return {r["file_path"]: r["file_hash"] for r in rows}

    def count_pushed_files(self, project_id: int) -> int:
        return self._e("SELECT COUNT(*) as cnt FROM pushed_files WHERE project_id = %s", (project_id,)).fetchone()["cnt"]

    def delete_pushed_file(self, project_id: int, file_path: str) -> None:
        self._e("DELETE FROM pushed_files WHERE project_id = %s AND file_path = %s", (project_id, file_path))
        self.conn.commit()

    def clear_pushed_files(self, project_id: int) -> None:
        self._e("DELETE FROM pushed_files WHERE project_id = %s", (project_id,))
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


class CursorProxy:
    def __init__(self, cur, pg: bool):
        self._cur = cur
        self._pg = pg

    def fetchone(self) -> Optional[dict]:
        r = self._cur.fetchone()
        if r is None:
            return None
        if self._pg:
            return dict(r)
        return r

    def fetchall(self) -> list[dict]:
        rows = self._cur.fetchall()
        if self._pg:
            return [dict(r) for r in rows]
        return rows

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def lastrowid(self) -> int:
        return self._cur.lastrowid


db = Database()
