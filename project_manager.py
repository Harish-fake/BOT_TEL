import os
from typing import Optional, Any
from database import db


class ProjectManagerError(Exception):
    pass


class ProjectManager:

    @staticmethod
    def create_project(
        user_id: int,
        project_name: str,
        project_path: str,
        github_account_id: Optional[int] = None,
    ) -> int:
        return db.add_project(user_id, project_name, project_path, github_account_id)

    @staticmethod
    def get_project(project_id: int) -> Optional[dict[str, Any]]:
        row = db.get_project(project_id)
        if row:
            return dict(row)
        return None

    @staticmethod
    def get_user_projects(user_id: int) -> list[dict[str, Any]]:
        rows = db.get_user_projects(user_id)
        return [dict(r) for r in rows]

    @staticmethod
    def link_github(
        project_id: int,
        github_account_id: int,
        repo_url: str,
    ) -> None:
        project = db.get_project(project_id)
        if not project:
            raise ProjectManagerError("Project not found.")
        db.set_project_github(project_id, github_account_id, repo_url)

    @staticmethod
    def set_schedule(project_id: int, cron_expr: str) -> None:
        project = db.get_project(project_id)
        if not project:
            raise ProjectManagerError("Project not found.")
        db.set_project_schedule(project_id, cron_expr)
        existing = db.get_schedule_by_project(project_id)
        if existing:
            db.update_schedule(existing["id"], cron_expr)
        else:
            db.add_schedule(project_id, cron_expr)

    @staticmethod
    def record_push(project_id: int) -> None:
        db.set_project_last_push(project_id)

    @staticmethod
    def log_sync(
        project_id: int,
        status: str,
        files_changed: int = 0,
        commit_hash: Optional[str] = None,
        duration_ms: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> int:
        return db.add_sync_log(
            project_id, status, files_changed, commit_hash, duration_ms, error_message
        )
