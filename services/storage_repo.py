import os
import shutil
import sqlite3
import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DB_DIR = os.path.join(os.getcwd(), "database")
STORAGE_DIR = os.path.join(os.getcwd(), "storage", "repo_backup")
DB_FILES = ["bot.db", "apscheduler.db"]

STORAGE_REPO_NAME = "gitsync-bot-storage"


def _default_storage_url() -> str:
    try:
        from git import Repo
        repo = Repo(os.getcwd())
        origin = repo.remotes.origin
        url = origin.url
        if "github.com" in url:
            parts = url.rstrip(".git").split("github.com/")
            if len(parts) == 2:
                owner = parts[1].split("/")[0]
                return f"https://github.com/{owner}/{STORAGE_REPO_NAME}.git"
    except Exception:
        pass
    return ""


def _safe_copy_db(src: str, dst: str) -> None:
    if not os.path.exists(src):
        return
    src_conn = sqlite3.connect(src, timeout=30)
    src_conn.execute("PRAGMA busy_timeout=30000")
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        dst_conn = sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


class StorageRepo:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._repo_url = os.environ.get("STORAGE_REPO_URL", "") or _default_storage_url()
        self._token = os.environ.get("STORAGE_REPO_TOKEN", "")
        self._enabled = bool(self._repo_url and self._token)
        self._dirty = False
        self._timer: threading.Timer = None
        self._repo: "Repo" = None
        if self._enabled:
            logger.info(f"Storage repo backup ENABLED → {self._repo_url}")
        else:
            logger.info("Storage repo backup DISABLED (set STORAGE_REPO_TOKEN).")

    def _auth_url(self) -> str:
        return self._repo_url.replace("https://", f"https://{self._token}@")

    def _get_repo(self):
        if self._repo is not None:
            return self._repo
        from git import Repo
        repo_path = os.path.abspath(STORAGE_DIR)
        if os.path.exists(os.path.join(repo_path, ".git")):
            self._repo = Repo(repo_path)
        else:
            os.makedirs(os.path.dirname(repo_path), exist_ok=True)
            if os.path.exists(repo_path):
                shutil.rmtree(repo_path)
            self._repo = Repo.clone_from(self._auth_url(), repo_path)
        return self._repo

    def restore(self) -> bool:
        if not self._enabled:
            return False
        repo_path = os.path.abspath(STORAGE_DIR)
        try:
            repo = self._get_repo()
            repo.remotes.origin.fetch()
            repo.git.reset("--hard", "origin/main")
            restored = False
            for fname in DB_FILES:
                stored = os.path.join(repo_path, fname)
                dst = os.path.join(_DB_DIR, fname)
                if os.path.exists(stored):
                    os.makedirs(_DB_DIR, exist_ok=True)
                    _safe_copy_db(stored, dst)
                    logger.info("Restored %s from storage repo.", fname)
                    restored = True
            if restored:
                return True
            logger.info("No existing database in storage repo (fresh start).")
            return False
        except Exception as e:
            logger.warning(f"Storage repo restore skipped: {e}")
            return False

    def is_enabled(self) -> bool:
        return self._enabled

    def mark_dirty(self) -> None:
        self._dirty = True

    def backup(self) -> bool:
        if not self._enabled:
            return False
        try:
            repo = self._get_repo()
            origin = repo.remotes.origin
            repo_path = os.path.abspath(STORAGE_DIR)
            copied = False
            for fname in DB_FILES:
                src = os.path.join(_DB_DIR, fname)
                if os.path.exists(src):
                    dst = os.path.join(repo_path, fname)
                    _safe_copy_db(src, dst)
                    copied = True
            if not copied:
                return False
            repo.index.add(DB_FILES)
            if repo.index.diff("HEAD") or repo.untracked_files:
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                repo.index.commit(f"backup {ts}")
                auth_url = self._auth_url()
                old_url = origin.url
                origin.set_url(auth_url)
                origin.push(refspec="main:main", force=True)
                origin.set_url(old_url)
                logger.info("Databases backed up to storage repo.")
            self._dirty = False
            return True
        except Exception as e:
            logger.error(f"Storage repo backup failed: {e}")
            return False

    def start_periodic_backup(self) -> None:
        if not self._enabled:
            return

        def _loop():
            if self._dirty:
                self.backup()
            self._timer = threading.Timer(300, _loop)
            self._timer.daemon = True
            self._timer.start()

        self._timer = threading.Timer(300, _loop)
        self._timer.daemon = True
        self._timer.start()
        logger.info("Periodic storage backup started (every 5 min).")

    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
        if self._dirty:
            self.backup()


storage_repo = StorageRepo()
