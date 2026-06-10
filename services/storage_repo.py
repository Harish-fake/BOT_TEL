import os
import shutil
import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STORAGE_DIR = os.path.join("storage", "repo_backup")
DB_SOURCE = os.path.join("database", "bot.db")
DB_FILE = "bot.db"


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
        self._repo_url = os.environ.get("STORAGE_REPO_URL", "")
        self._token = os.environ.get("STORAGE_REPO_TOKEN", "")
        self._enabled = bool(self._repo_url and self._token)
        self._dirty = False
        self._timer: threading.Timer = None
        self._repo: "Repo" = None
        if self._enabled:
            logger.info("Storage repo backup is ENABLED.")
        else:
            logger.info("Storage repo backup is DISABLED (set STORAGE_REPO_URL and STORAGE_REPO_TOKEN).")

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
        db_path = os.path.abspath(DB_SOURCE)
        try:
            repo = self._get_repo()
            repo.remotes.origin.fetch()
            repo.git.reset("--hard", "origin/main")
            stored_db = os.path.join(os.path.abspath(STORAGE_DIR), DB_FILE)
            if os.path.exists(stored_db):
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                shutil.copy2(stored_db, db_path)
                logger.info("Database restored from storage repo.")
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
        db_path = os.path.abspath(DB_SOURCE)
        if not os.path.exists(db_path):
            return False
        try:
            repo = self._get_repo()
            origin = repo.remotes.origin
            repo_path = os.path.abspath(STORAGE_DIR)
            shutil.copy2(db_path, os.path.join(repo_path, DB_FILE))
            repo.index.add([DB_FILE])
            if repo.index.diff("HEAD") or repo.untracked_files:
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                repo.index.commit(f"backup {ts}")
                auth_url = self._auth_url()
                old_url = origin.url
                origin.set_url(auth_url)
                origin.push(refspec="main:main", force=True)
                origin.set_url(old_url)
                logger.info("Database backed up to storage repo.")
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
