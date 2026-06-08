from typing import Optional


class ReportService:

    @staticmethod
    def analysis_report(
        project_name: str,
        files: int,
        folders: int,
        loc: int,
        technologies: list[str],
    ) -> str:
        tech_str = "\n".join(f"  • {t}" for t in technologies) if technologies else "  None detected"
        return (
            f"📊 *Project Analysis*\n\n"
            f"*Project Name:* {project_name}\n\n"
            f"*Files:* {files}\n"
            f"*Folders:* {folders}\n"
            f"*LOC:* {loc:,}\n\n"
            f"*Detected Technologies:*\n{tech_str}"
        )

    @staticmethod
    def push_report(
        repo_name: str,
        files_changed: int,
        commit_hash: Optional[str],
        duration_ms: float,
        status: str,
        error_message: Optional[str] = None,
    ) -> str:
        if status == "success":
            return (
                f"✅ *Sync Completed*\n\n"
                f"*Repository:* {repo_name}\n"
                f"*Files Changed:* {files_changed}\n"
                f"*Commit:* `{commit_hash or 'N/A'}`\n"
                f"*Duration:* {duration_ms:.1f}s\n"
                f"*Status:* Success"
            )
        else:
            return (
                f"❌ *Sync Failed*\n\n"
                f"*Repository:* {repo_name}\n"
                f"*Error:* {error_message or 'Unknown error'}\n"
                f"*Duration:* {duration_ms:.1f}s"
            )

    @staticmethod
    def no_changes_report(repo_name: str) -> str:
        return (
            f"ℹ️ *Sync Skipped*\n\n"
            f"*Repository:* {repo_name}\n"
            f"No changes detected. Nothing to commit."
        )

    @staticmethod
    def status_report(
        project_name: str,
        github_repo: Optional[str],
        last_push: Optional[str],
        next_push: Optional[str],
        schedule: Optional[str],
        branch: str = "main",
    ) -> str:
        return (
            f"📋 *Project Status*\n\n"
            f"*Project:* {project_name}\n"
            f"*Repository:* {github_repo or 'Not linked'}\n"
            f"*Branch:* {branch}\n"
            f"*Last Push:* {last_push or 'Never'}\n"
            f"*Next Push:* {next_push or 'Not scheduled'}\n"
            f"*Schedule:* {schedule or 'Not set'}"
        )

    @staticmethod
    def changed_files_report(files: list[dict]) -> str:
        if not files:
            return "No changes detected."
        lines = ["*Changed Files*\n"]
        for f in files:
            if f["status"] == "created":
                lines.append(f"  + {f['path']}")
            elif f["status"] == "deleted":
                lines.append(f"  - {f['path']}")
            else:
                lines.append(f"  ~ {f['path']}")
        return "\n".join(lines)

    @staticmethod
    def projects_list(projects: list[dict]) -> str:
        if not projects:
            return "No projects found."
        lines = ["*Your Projects*\n"]
        for p in projects:
            status = "🔗 GitHub" if p.get("github_repo") else "📁 Local"
            lines.append(f"  {p['id']}. {p['project_name']} [{status}]")
        return "\n".join(lines)

    @staticmethod
    def github_accounts_list(accounts: list[dict]) -> str:
        if not accounts:
            return "No GitHub accounts linked."
        lines = ["*Linked GitHub Accounts*\n"]
        for a in accounts:
            lines.append(f"  {a['id']}. {a['account_alias']} — `{a['github_username']}`")
        return "\n".join(lines)
