import os
from typing import Optional
from git import Repo, InvalidGitRepositoryError
from analyzer import ProjectAnalyzer


class GitHubManagerError(Exception):
    pass


class GitHubManager:

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

        if not repo.head.is_valid():
            techs = ProjectAnalyzer().analyze(project_path)["technologies"]
            gitignore_content = ProjectAnalyzer.generate_gitignore(techs)
            gitignore_path = os.path.join(project_path, ".gitignore")
            if not os.path.exists(gitignore_path):
                with open(gitignore_path, "w") as f:
                    f.write(gitignore_content)
                repo.index.add([".gitignore"])
                repo.index.commit("Initial commit")

        try:
            repo.active_branch.name
        except TypeError:
            repo.git.branch("-M", branch)

        origin = repo.remotes.origin
        auth_url = repo_url.replace("https://", f"https://{token}@")
        origin.set_url(auth_url)

        try:
            if repo.head.is_valid():
                push_info = origin.push(refspec=f"{branch}:{branch}")
                if push_info and push_info[0] and push_info[0].flags & 128:
                    raise GitHubManagerError(
                        f"Push rejected: {push_info[0].summary}"
                    )
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
