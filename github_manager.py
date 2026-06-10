import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from git import Repo, InvalidGitRepositoryError, GitCommandError
from analyzer import ProjectAnalyzer

IST = timezone(timedelta(hours=5, minutes=30))


class GitHubManagerError(Exception):
    pass


class GitHubManager:

    @staticmethod
    def _has_commits(repo: Repo) -> bool:
        try:
            repo.head.commit
            return True
        except (ValueError, KeyError, IndexError):
            return False

    @staticmethod
    def init_repo(
        project_path: str,
        repo_url: str,
        token: str,
        branch: str = "main",
    ) -> dict:
        try:
            repo = Repo(project_path)
        except InvalidGitRepositoryError:
            repo = Repo.init(project_path, initial_branch=branch)

        if "origin" in [r.name for r in repo.remotes]:
            origin = repo.remotes.origin
            origin.set_url(repo_url)
        else:
            origin = repo.create_remote("origin", repo_url)

        # Ensure at least one commit exists so 'main' branch is valid
        if not GitHubManager._has_commits(repo):
            techs = ProjectAnalyzer().analyze(project_path).get("technologies", [])
            gitignore_content = ProjectAnalyzer.generate_gitignore(techs)
            gitignore_path = os.path.join(project_path, ".gitignore")
            with open(gitignore_path, "w") as f:
                f.write(gitignore_content)

            # Write a unique marker file to guarantee a change exists
            marker_path = os.path.join(project_path, ".gitsync")
            now = datetime.now(IST).strftime("%Y-%m-%d %I:%M %p IST")
            with open(marker_path, "w") as f:
                f.write(f"GitSync Bot — initialized {now}\n")

            repo.index.add([".gitignore", ".gitsync"])
            try:
                repo.index.commit("Initial commit")
            except Exception:
                repo.index.add(A=True)
                repo.index.commit("Initial commit")

        # Force the branch name
        try:
            current = repo.active_branch.name
            if current != branch:
                repo.git.branch("-M", branch)
        except TypeError:
            repo.git.branch("-M", branch)

        # Auth and push
        auth_url = repo_url.replace("https://", f"https://{token}@")
        origin.set_url(auth_url)

        try:
            try:
                origin.fetch()
                try:
                    repo.git.rebase(f"origin/{branch}")
                except GitCommandError:
                    repo.git.merge(f"origin/{branch}", "--no-edit")
            except Exception:
                pass

            push_info = origin.push(refspec=f"{branch}:{branch}")
            if push_info and push_info[0] and push_info[0].flags & 128:
                raise GitHubManagerError(f"Push rejected: {push_info[0].summary}")
        except GitHubManagerError:
            raise
        except Exception as e:
            origin.set_url(repo_url)
            raise GitHubManagerError(f"Failed to push initial commit: {e}")

        origin.set_url(repo_url)

        return {
            "success": True,
            "repo_url": repo_url,
            "branch": branch,
        }

    @staticmethod
    def validate_repo_url(url: str) -> bool:
        if not url:
            return False
        url = url.strip()
        if url.startswith("https://github.com/") or url.startswith("git@github.com:"):
            return True
        return False

    @staticmethod
    def extract_repo_name(url: str) -> str:
        url = url.strip()
        if url.endswith(".git"):
            url = url[:-4]
        if "github.com/" in url:
            parts = url.split("github.com/")[-1]
            return parts
        return url
