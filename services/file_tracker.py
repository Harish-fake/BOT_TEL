import os
import hashlib
from typing import Optional
from database import db


DEFAULT_BATCH_SIZE = 4


class FileTracker:

    @staticmethod
    def compute_hash(file_path: str) -> str:
        h = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
        except (OSError, IOError):
            pass
        return h.hexdigest()

    @staticmethod
    def get_pending_files(project_path: str, project_id: int) -> list[dict]:
        pushed = db.get_pushed_file_hashes(project_id)
        pending = []

        for root, dirs, files in os.walk(project_path):
            if ".git" in dirs:
                dirs.remove(".git")
            for fname in files:
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, project_path)
                fhash = FileTracker.compute_hash(fpath)
                if rel not in pushed or pushed[rel] != fhash:
                    pending.append({
                        "path": fpath,
                        "relative": rel,
                        "hash": fhash,
                    })

        return pending

    @staticmethod
    def get_next_batch(project_path: str, project_id: int, batch_size: int = DEFAULT_BATCH_SIZE) -> list[dict]:
        pending = FileTracker.get_pending_files(project_path, project_id)
        return pending[:batch_size]

    @staticmethod
    def record_pushed(project_id: int, files: list[dict]) -> None:
        for f in files:
            db.record_pushed_file(project_id, f["relative"], f["hash"])

    @staticmethod
    def get_progress(project_path: str, project_id: int) -> dict:
        pushed_count = db.count_pushed_files(project_id)
        total = 0
        for root, dirs, files in os.walk(project_path):
            if ".git" in dirs:
                dirs.remove(".git")
            total += len(files)
        remaining = total - pushed_count
        percent = round(pushed_count / total * 100, 1) if total else 100
        return {
            "total": total,
            "pushed": pushed_count,
            "remaining": max(0, remaining),
            "percent": min(100, percent),
        }

    @staticmethod
    def get_changed_since_last_push(project_path: str, project_id: int) -> list[dict]:
        pending = FileTracker.get_pending_files(project_path, project_id)
        return pending
