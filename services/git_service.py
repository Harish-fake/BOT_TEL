import os
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
from typing import Optional
from git import Repo, InvalidGitRepositoryError, GitCommandError
from git.exc import NoSuchPathError


class GitServiceError(Exception):
    pass


class GitService:

    @staticmethod
    def init_repo(project_path: str, branch: str = "main") -> Repo:
        try:
            repo = Repo(project_path)
        except InvalidGitRepositoryError:
            repo = Repo.init(project_path, initial_branch=branch)

        try:
            repo.head.commit
        except Exception:
            gitignore_path = os.path.join(project_path, ".gitignore")
            if not os.path.exists(gitignore_path):
                try:
                    with open(gitignore_path, "w") as f:
                        f.write("*.pyc\n__pycache__/\n.env\n.venv/\nvenv/\n")
                except Exception:
                    pass
            marker_path = os.path.join(project_path, ".gitsync")
            try:
                with open(marker_path, "w") as f:
                    f.write(f"GitSync Bot — {datetime.now(IST).strftime('%Y-%m-%d %I:%M %p IST')}\n")
            except Exception:
                pass
            try:
                repo.index.add(A=True)
                repo.index.commit("Initial commit")
            except Exception:
                pass

        try:
            active = repo.active_branch
            if active.name != branch:
                repo.git.branch("-M", branch)
        except TypeError:
            repo.git.branch("-M", branch)

        return repo

    @staticmethod
    def get_changed_files(project_path: str) -> list[dict]:
        repo = GitService.init_repo(project_path)
        changes = []

        if repo.is_dirty(untracked_files=True):
            diff = repo.index.diff(None)
            for d in diff:
                changes.append({"path": d.a_path, "status": "modified"})
            untracked = repo.untracked_files
            for u in untracked:
                changes.append({"path": u, "status": "created"})

        if repo.head.is_valid():
            try:
                for d in repo.head.commit.diff("HEAD~1"):
                    status = "deleted" if d.change_type == "D" else "modified"
                    changes.append({"path": d.b_path or d.a_path, "status": status})
            except Exception:
                pass

        seen = set()
        unique = []
        for c in changes:
            key = c["path"]
            if key not in seen:
                seen.add(key)
                unique.append(c)
        return unique

    @staticmethod
    def _ensure_remote(repo: Repo, repo_url: str, token: str, branch: str) -> None:
        auth_url = repo_url.replace("https://", f"https://{token}@")
        if "origin" in [r.name for r in repo.remotes]:
            origin = repo.remotes.origin
            current_url = origin.url
            if "https://" in current_url and "@" not in current_url:
                origin.set_url(auth_url)
            try:
                origin.fetch()
                if repo.head.is_valid():
                    try:
                        repo.git.rebase(f"origin/{branch}")
                    except GitCommandError:
                        repo.git.merge(f"origin/{branch}", "--no-edit")
            except GitCommandError:
                pass
        else:
            origin = repo.create_remote("origin", auth_url)
        if origin.url != repo_url:
            origin.set_url(repo_url)

    @staticmethod
    def _do_push(repo: Repo, repo_url: str, token: str, branch: str) -> None:
        origin = repo.remotes.origin
        auth_url = repo_url.replace("https://", f"https://{token}@")
        origin.set_url(auth_url)

        try:
            push_info = origin.push(refspec=f"{branch}:{branch}")
            if push_info and push_info[0] and push_info[0].flags & 128:
                raise GitServiceError(f"Push rejected: {push_info[0].summary}")
        except GitCommandError as e:
            raise GitServiceError(f"Push failed: {e}")
        finally:
            origin.set_url(repo_url)

    @staticmethod
    def commit_and_push(
        project_path: str,
        token: str,
        repo_url: str,
        project_name: str = "",
        branch: str = "main",
        github_username: str = "",
    ) -> dict:
        repo = GitService.init_repo(project_path, branch)
        GitService._ensure_remote(repo, repo_url, token, branch)

        repo.git.add(A=True)

        if not repo.index.diff("HEAD"):
            return {"changed": False, "message": "No changes to commit."}

        timestamp = datetime.now(IST).strftime("%Y-%m-%d %I:%M %p IST")
        author = github_username or project_name or "GitSync Bot"
        commit_msg = f"Uploaded via {author} — {timestamp}"
        commit = repo.index.commit(commit_msg)

        GitService._do_push(repo, repo_url, token, branch)

        files_changed = (
            len(repo.head.commit.diff(repo.head.commit.parents[0]))
            if repo.head.commit.parents
            else 1
        )

        return {
            "changed": True,
            "files_changed": files_changed,
            "commit_hash": commit.hexsha[:7],
            "message": "Push successful.",
        }

    @staticmethod
    def batch_commit_and_push(
        project_path: str,
        token: str,
        repo_url: str,
        files: list[dict],
        project_name: str = "",
        branch: str = "main",
        github_username: str = "",
    ) -> dict:
        if not files:
            return {"changed": False, "message": "No files to push.", "files_count": 0}

        repo = GitService.init_repo(project_path, branch)
        GitService._ensure_remote(repo, repo_url, token, branch)

        rel_paths = [f["relative"] for f in files]
        repo.index.add(rel_paths)

        commit_hash = None
        has_diff = bool(repo.index.diff("HEAD"))

        if not has_diff and repo.untracked_files:
            extra = [f for f in repo.untracked_files if f not in rel_paths]
            if extra:
                repo.index.add(extra)
                rel_paths.extend(extra)
                has_diff = bool(repo.index.diff("HEAD"))

        if has_diff:
            timestamp = datetime.now(IST).strftime("%Y-%m-%d %I:%M %p IST")
            author = github_username or project_name or "GitSync Bot"
            commit_msg = f"Uploaded via {author} — {timestamp}"
            try:
                commit = repo.index.commit(commit_msg)
                commit_hash = commit.hexsha[:7]
            except GitCommandError:
                pass

        # Check for unpushed commits (new commit or leftover from a prior failed push)
        unpushed = []
        try:
            unpushed = list(repo.iter_commits(f"origin/{branch}..{branch}"))
        except Exception:
            pass
        if commit_hash or unpushed:
            try:
                GitService._do_push(repo, repo_url, token, branch)
                if not commit_hash and unpushed:
                    commit_hash = unpushed[0].hexsha[:7]
            except GitServiceError as e:
                if not commit_hash:
                    raise
                # New commit was made but push failed — return hash so caller can retry later

        return {
            "changed": commit_hash is not None,
            "files_changed": len(rel_paths) if commit_hash else 0,
            "commit_hash": commit_hash,
            "files_count": len(rel_paths),
            "message": "Push successful." if commit_hash else "Files already synced.",
        }

    @staticmethod
    def has_remote(project_path: str) -> bool:
        try:
            repo = Repo(project_path)
            return "origin" in [r.name for r in repo.remotes]
        except (InvalidGitRepositoryError, NoSuchPathError):
            return False
