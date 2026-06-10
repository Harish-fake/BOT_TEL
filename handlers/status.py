import time
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler

from database import db
from project_manager import ProjectManager
from scheduler import scheduler_manager, now_ist, format_ist
from services.git_service import GitService, GitServiceError
from services.encryption_service import EncryptionService
from services.report_service import ReportService
from services.schedule_service import ScheduleService
from services.file_tracker import FileTracker, DEFAULT_BATCH_SIZE

logger = logging.getLogger(__name__)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    user_db = db.get_user_by_telegram_id(user.id)
    if not user_db:
        await update.message.reply_text("Please use /start first.")
        return

    projects = db.get_user_projects(user_db["id"])
    if not projects:
        await update.message.reply_text("No projects found. Use /upload to add one.")
        return

    if context.args:
        try:
            project_id = int(context.args[0])
            project = db.get_project(project_id)
            if not project or project["user_id"] != user_db["id"]:
                await update.message.reply_text("Project not found.")
                return
            await show_project_status(update, project)
        except ValueError:
            await update.message.reply_text("Usage: /status <project_id>")
    else:
        for p in projects:
            await show_project_status(update, p)


async def show_project_status(update: Update, project: dict) -> None:
    schedule = db.get_schedule_by_project(project["id"])
    cron_expr = schedule.get("cron_expression") if schedule else project.get("schedule_time")
    schedule_desc = ScheduleService.describe_cron(cron_expr) if cron_expr else None
    paused = bool(schedule and not schedule.get("enabled", 1))

    next_push = None if paused else scheduler_manager.get_next_run_time(project["id"])

    last_push = project.get("last_push")
    if last_push:
        try:
            dt = datetime.fromisoformat(last_push.replace("Z", "+00:00"))
            last_push = format_ist(dt)
        except Exception:
            if "T" in last_push:
                last_push = last_push.replace("T", " ")[:19]

    progress = FileTracker.get_progress(project["project_path"], project["id"])
    batch_size = db.get_batch_size(project["id"])
    bar = _progress_bar(progress['pushed'], progress['total'])
    progress_line = f"Uploaded: {progress['pushed']}/{progress['total']} files ({progress['percent']:.0f}%)"
    progress_line += f"\n{bar}"
    if progress["remaining"] > 0:
        progress_line += f"\nRemaining: ~{progress['remaining']} files | Batch: {batch_size} files"
        progress_line += f"\n/batchsize to change batch size"

    report = ReportService.status_report(
        project_name=project["project_name"],
        github_repo=project.get("github_repo"),
        last_push=last_push,
        next_push=next_push,
        schedule=schedule_desc,
    )
    report = f"🕐 *Current Time:* {now_ist()}\n" + report
    if paused:
        report += "\n⏸ *Sync Paused*"
    report += f"\n\n📊 *Progress:*\n{progress_line}"

    logs = db.get_sync_logs(project["id"], limit=5)
    if logs:
        report += "\n\n*Recent Syncs:*"
        for log in logs:
            status_icon = "✅" if log.get("status") == "success" else "❌"
            ts_raw = log.get("created_at", "")
            ts = str(ts_raw)
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts = format_ist(dt)
            except Exception:
                if "T" in ts:
                    ts = ts.replace("T", " ")[:19]
            report += f"\n{status_icon} {ts} — {log.get('files_changed', 0)} files, {log.get('commit_hash', 'N/A') or 'N/A'}"

    controls = []
    if schedule:
        if paused:
            controls.append(InlineKeyboardButton("▶ Resume", callback_data=f"resume:{project['id']}"))
        else:
            controls.append(InlineKeyboardButton("⏸ Pause", callback_data=f"pause:{project['id']}"))
    keyboard = [
        [InlineKeyboardButton("🚀 Push Next Batch", callback_data=f"pushnow:{project['id']}")] + controls,
        [InlineKeyboardButton("📤 Push All Remaining", callback_data=f"pushall:{project['id']}")],
        [InlineKeyboardButton("📁 Browse", callback_data=f"browse_project:{project['id']}")],
        [InlineKeyboardButton("📊 Sync History", callback_data=f"synclogs:{project['id']}")],
    ]
    await update.message.reply_text(
        report,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def pushnow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    user_db = db.get_user_by_telegram_id(user.id)
    if not user_db:
        await update.message.reply_text("Please use /start first.")
        return

    projects = db.get_user_projects(user_db["id"])
    projects_with_github = [p for p in projects if p.get("github_repo") and p.get("github_account_id")]

    if not projects_with_github:
        await update.message.reply_text(
            "No projects linked to GitHub. Use /github to link one."
        )
        return

    if context.args:
        try:
            project_id = int(context.args[0])
            project = db.get_project(project_id)
            if not project or project["user_id"] != user_db["id"] or not project.get("github_repo"):
                await update.message.reply_text("Project not found or not linked to GitHub.")
                return
            await do_push(update, project)
        except ValueError:
            await update.message.reply_text("Usage: /pushnow <project_id>")
    else:
        for p in projects_with_github:
            await do_push(update, p)


async def do_push(update: Update, project: dict) -> None:
    msg = await update.message.reply_text(f"🔄 Pushing {project['project_name']}...")

    account = db.get_github_account(project["github_account_id"])
    if not account:
        await msg.edit_text(f"❌ GitHub account not found for {project['project_name']}.")
        return

    try:
        token = EncryptionService.decrypt(account["token_encrypted"])
    except Exception:
        await msg.edit_text("❌ Failed to decrypt GitHub token.")
        return

    project_path = project["project_path"]
    project_name = project["project_name"]
    project_id = project["id"]
    github_username = account.get("github_username", "")

    batch_size = db.get_batch_size(project_id)
    batch = FileTracker.get_next_batch(project_path, project_id, batch_size=batch_size)
    if not batch:
        progress = FileTracker.get_progress(project_path, project_id)
        if progress["pushed"] >= progress["total"]:
            await msg.edit_text(f"✅ All {progress['total']} files already synced. No pending changes.")
        else:
            await msg.edit_text("No changes to push.")
        return

    start_time = time.time()
    try:
        result = GitService.batch_commit_and_push(
            project_path,
            token,
            project["github_repo"],
            batch,
            project_name=project_name,
            github_username=github_username,
        )

        duration = time.time() - start_time

        commit_str = result.get("commit_hash")
        if commit_str:
            FileTracker.record_pushed(project_id, batch)
            ProjectManager.record_push(project_id)
            ProjectManager.log_sync(
                project_id, "success",
                files_changed=result.get("files_changed", 0),
                commit_hash=commit_str,
                duration_ms=int(duration * 1000),
            )
            progress = FileTracker.get_progress(project_path, project_id)
            report = (
                f"✅ Synced {len(batch)} files [{project_name}]\n"
                f"Commit: `{commit_str}`\n"
                f"Duration: {duration:.1f}s\n"
                f"Progress: {progress['pushed']}/{progress['total']} files ({progress['percent']:.0f}%)"
            )
            if progress["remaining"] > 0:
                report += f"\nRemaining: ~{progress['remaining']} files"
        else:
            ProjectManager.log_sync(
                project_id, "no_changes",
                duration_ms=int(duration * 1000),
            )
            report = f"ℹ️ No changes to push for {project_name}."

        await msg.edit_text(report)

    except GitServiceError as e:
        duration = time.time() - start_time
        ProjectManager.log_sync(
            project["id"],
            "failure",
            error_message=str(e),
            duration_ms=int(duration * 1000),
        )
        report = ReportService.push_report(
            repo_name=project["project_name"],
            files_changed=0,
            commit_hash=None,
            duration_ms=duration,
            status="failure",
            error_message=str(e),
        )
        await msg.edit_text(report, parse_mode="Markdown")
    except Exception as e:
        logger.exception(f"Push failed for {project['project_name']}")
        await msg.edit_text(f"❌ Unexpected error: {e}")


def _progress_bar(pushed: int, total: int, width: int = 10) -> str:
    if total <= 0:
        return "⬜" * width
    filled = int((pushed / total) * width)
    return "🟩" * filled + "⬜" * (width - filled)


async def do_push_all(update: Update, project: dict) -> None:
    query = update.callback_query
    await query.answer()
    msg = await query.edit_message_text(f"📤 Pushing ALL remaining files for {project['project_name']}...")

    account = db.get_github_account(project["github_account_id"])
    if not account:
        await msg.edit_text(f"❌ GitHub account not found.")
        return

    try:
        token = EncryptionService.decrypt(account["token_encrypted"])
    except Exception:
        await msg.edit_text("❌ Failed to decrypt token.")
        return

    project_path = project["project_path"]
    project_id = project["id"]
    github_username = account.get("github_username", "")

    all_pending = FileTracker.get_pending_files(project_path, project_id)
    if not all_pending:
        await msg.edit_text("✅ All files already synced. Nothing to push.")
        return

    start_time = time.time()
    try:
        result = GitService.batch_commit_and_push(
            project_path,
            token,
            project["github_repo"],
            all_pending,
            project_name=project["project_name"],
            github_username=github_username,
        )
        duration = time.time() - start_time

        commit_str = result.get("commit_hash")
        if commit_str:
            FileTracker.record_pushed(project_id, all_pending)
            ProjectManager.record_push(project_id)
            ProjectManager.log_sync(
                project_id, "success",
                files_changed=result.get("files_changed", 0),
                commit_hash=commit_str,
                duration_ms=int(duration * 1000),
            )
            progress = FileTracker.get_progress(project_path, project_id)
            report = (
                f"✅ *Bulk Push Complete!*\n\n"
                f"📤 Pushed {len(all_pending)} files in one batch.\n"
                f"Commit: `{commit_str}`\n"
                f"Duration: {duration:.1f}s\n"
                f"Progress: {progress['pushed']}/{progress['total']} files"
            )
        else:
            report = f"ℹ️ No changes to push for {project['project_name']}."

        await msg.edit_text(report, parse_mode="Markdown")

    except GitServiceError as e:
        duration = time.time() - start_time
        ProjectManager.log_sync(
            project["id"], "failure",
            error_message=str(e),
            duration_ms=int(duration * 1000),
        )
        await msg.edit_text(f"❌ Push failed: {e}")
    except Exception as e:
        logger.exception(f"Bulk push failed for {project['project_name']}")
        await msg.edit_text(f"❌ Unexpected error: {e}")


async def pushall_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    user_db = db.get_user_by_telegram_id(user.id)
    if not user_db:
        await update.message.reply_text("Please use /start first.")
        return
    projects = db.get_user_projects(user_db["id"])
    projects_with_github = [p for p in projects if p.get("github_repo") and p.get("github_account_id")]
    if not projects_with_github:
        await update.message.reply_text("No projects linked to GitHub. Use /github to link one.")
        return
    if context.args:
        try:
            project_id = int(context.args[0])
            project = db.get_project(project_id)
            if not project or project["user_id"] != user_db["id"] or not project.get("github_repo"):
                await update.message.reply_text("Project not found or not linked.")
                return
            await do_push_all(update, project)
        except ValueError:
            await update.message.reply_text("Usage: /pushall <project_id>")
    else:
        for p in projects_with_github:
            await do_push_all(update, p)


async def projects_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    user_db = db.get_user_by_telegram_id(user.id)
    if not user_db:
        await update.message.reply_text("Please use /start first.")
        return

    projects = db.get_user_projects(user_db["id"])
    if not projects:
        await update.message.reply_text("No projects yet. Use /upload.")
        return

    projects_data = []
    for p in projects:
        prog = FileTracker.get_progress(p["project_path"], p["id"])
        bar = _progress_bar(prog['pushed'], prog['total'])
        status_icon = "🔗" if p.get("github_repo") else "📁"
        projects_data.append({
            "id": p["id"],
            "project_name": p["project_name"],
            "github_repo": p.get("github_repo"),
            "progress": f"{bar} {prog['pushed']}/{prog['total']}",
            "status_icon": status_icon,
        })

    lines = ["*Your Projects*\n"]
    for pd in projects_data:
        lines.append(f"{pd['status_icon']} *{pd['project_name']}*")
        lines.append(f"  {pd['progress']}")
        if pd.get("github_repo"):
            lines.append(f"  Repo: `{pd['github_repo']}`")
        lines.append("")
    report = "\n".join(lines)

    keyboard = []
    for p in projects:
        keyboard.append([
            InlineKeyboardButton(
                f"📂 {p['project_name']}",
                callback_data=f"select_project:{p['id']}",
            )
        ])

    await update.message.reply_text(
        report,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )
