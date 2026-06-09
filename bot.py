#!/usr/bin/env python3
import os
import sys
import socket
import asyncio
import http.server
import threading
import warnings
import logging
from logging.handlers import RotatingFileHandler

warnings.filterwarnings("ignore", message="If 'per_message=False'")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)

from config import config
from database import db
from scheduler import scheduler_manager

# ── Handlers ──────────────────────────────────────────────
from handlers.start import start, help_command, about
from handlers.upload import upload_start, receive_zip, cancel, WAITING_FOR_ZIP
from handlers.browse import (
    select_project_callback as browse_select_project,
    get_browse_conversation_handler,
)
from handlers.accounts import (
    accounts_list,
    delete_account_callback,
    confirm_delete_account,
    cancel_del_account,
    get_add_account_handler,
)
from handlers.github import get_github_handler
from handlers.status import status_command, pushnow_command, projects_command
from handlers.settings import get_schedule_handler
from handlers.admin import admin_users, admin_projects, admin_stats, admin_logs, admin_broadcast
from handlers.upload_web import webupload_command

# ── Sync callback (used by scheduler) ─────────────────────
from project_manager import ProjectManager
from services.git_service import GitService, GitServiceError
from services.encryption_service import EncryptionService
from services.report_service import ReportService
from services.file_service import FileService
from services.file_tracker import FileTracker
import time
from datetime import datetime
from typing import Optional


def setup_logging() -> None:
    os.makedirs(os.path.join("storage", "logs"), exist_ok=True)
    log_path = os.path.join("storage", "logs", "bot.log")

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    file_handler = RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


async def scheduled_sync(project_id: int) -> None:
    logger = logging.getLogger(__name__)
    logger.info(f"Scheduled sync triggered for project {project_id}")
    project = db.get_project(project_id)
    if not project:
        logger.warning(f"Project {project_id} not found for scheduled sync.")
        return

    if not project.get("github_repo") or not project.get("github_account_id"):
        logger.info(f"Project {project_id} not linked to GitHub, skipping sync.")
        return

    account = db.get_github_account(project["github_account_id"])
    if not account:
        logger.warning(f"GitHub account not found for project {project_id}.")
        return

    try:
        token = EncryptionService.decrypt(account["token_encrypted"])
    except Exception as e:
        logger.error(f"Failed to decrypt token for project {project_id}: {e}")
        return

    project_path = project["project_path"]
    project_name = project["project_name"]

    github_username = account.get("github_username", "")

    batch_size = db.get_batch_size(project_id)
    batch = FileTracker.get_next_batch(project_path, project_id, batch_size=batch_size)
    if not batch:
        if project_id in _synced_notified:
            return
        _synced_notified.add(project_id)
        progress = FileTracker.get_progress(project_path, project_id)
        if progress["total"] == 0:
            return
        if progress["pushed"] >= progress["total"]:
            report = f"✅ All {progress['total']} files synced. No pending changes."
        else:
            report = ReportService.no_changes_report(project_name)
        user = db.get_user(project["user_id"])
        if user:
            try:
                bot = application.bot
                await bot.send_message(chat_id=user["telegram_id"], text=report)
            except Exception as e:
                logger.warning(f"Failed to send report: {e}")
        return

    start = time.time()
    try:
        result = GitService.batch_commit_and_push(
            project_path,
            token,
            project["github_repo"],
            batch,
            project_name=project_name,
            github_username=github_username,
        )
        duration = time.time() - start

        _synced_notified.discard(project_id)
        commit_str = result.get("commit_hash")

        if commit_str:
            FileTracker.record_pushed(project_id, batch)
            ProjectManager.record_push(project["id"])
            ProjectManager.log_sync(
                project["id"], "success",
                files_changed=result.get("files_changed", 0),
                commit_hash=commit_str,
                duration_ms=int(duration * 1000),
            )

            progress = FileTracker.get_progress(project_path, project_id)
            report = (
                f"✅ Synced {len(batch)} files [{project_name}]\n"
                f"Commit: `{commit_str}`\n"
                f"Duration: {duration:.1f}s\n"
                f"Progress: {progress['pushed']}/{progress['total']} files "
                f"({progress['percent']:.0f}%)"
            )
            if progress["remaining"] > 0:
                next_run = scheduler_manager.get_next_run_time(project_id)
                report += f"\nRemaining: ~{progress['remaining']} files"
                report += f"\nNext batch: {next_run or 'next schedule tick'}"
        else:
            ProjectManager.log_sync(
                project["id"], "no_changes",
                duration_ms=int(duration * 1000),
            )
            report = f"ℹ️ No changes to push for {project_name}."

        user = db.get_user(project["user_id"])
        if user:
            try:
                bot = application.bot
                await bot.send_message(chat_id=user["telegram_id"], text=report)
            except Exception as e:
                logger.warning(f"Failed to send sync report: {e}")

    except GitServiceError as e:
        duration = time.time() - start
        logger.error(f"Batch push failed: {e}")
        ProjectManager.log_sync(
            project["id"], "failure",
            error_message=str(e),
            duration_ms=int(duration * 1000),
        )
        user = db.get_user(project["user_id"])
        if user:
            try:
                bot = application.bot
                await bot.send_message(
                    chat_id=user["telegram_id"],
                    text=f"❌ Push failed for {project_name}: {e}",
                )
            except Exception:
                pass
    except Exception as e:
        logger.exception(f"Unexpected error in scheduled sync for project {project_id}")


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("📤 Upload Project", callback_data="upload")],
        [InlineKeyboardButton("📋 My Projects", callback_data="list_projects")],
        [InlineKeyboardButton("👤 GitHub Accounts", callback_data="list_accounts")],
        [InlineKeyboardButton("⏰ Set Schedule", callback_data="set_schedule")],
    ]
    await query.edit_message_text(
        "🏠 *Main Menu*\n\nChoose an option:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def main_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("📤 Upload Project", callback_data="upload")],
        [InlineKeyboardButton("📋 My Projects", callback_data="list_projects")],
        [InlineKeyboardButton("👤 GitHub Accounts", callback_data="list_accounts")],
        [InlineKeyboardButton("⏰ Set Schedule", callback_data="set_schedule")],
    ]
    await update.message.reply_text(
        "🏠 *Main Menu*\n\nChoose an option:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_callback_routing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data

    if data == "main_menu":
        await main_menu_callback(update, context)
    elif data == "upload":
        query = update.callback_query
        await query.answer()
        await query.message.reply_text("Use /upload to upload a project ZIP file.")
    elif data == "list_accounts":
        query = update.callback_query
        await query.answer()
        from handlers.accounts import accounts_list as acct_list
        await acct_list(update, context)
    elif data == "set_schedule":
        query = update.callback_query
        await query.answer()
        await query.message.reply_text("Use /schedule to set auto-sync schedule.")
    elif data == "link_github":
        query = update.callback_query
        await query.answer()
        await query.message.reply_text("Use /github to link a GitHub repository.")
    elif data == "show_status":
        query = update.callback_query
        await query.answer()
        project_id = context.user_data.get("current_project_id")
        if project_id:
            project = db.get_project(project_id)
            if project:
                from handlers.status import show_project_status
                await show_project_status(update, project)
    elif data == "show_help":
        query = update.callback_query
        await query.answer()
        from handlers.start import help_command
        context.args = []
        await help_command(update, context)
    elif data == "noop":
        query = update.callback_query
        await query.answer()
    elif data.startswith("synclogs:"):
        query = update.callback_query
        await query.answer()
        project_id = int(data.split(":")[1])
        project = db.get_project(project_id)
        if project:
            logs = db.get_sync_logs(project_id, limit=10)
            if not logs:
                text = "No sync history yet."
            else:
                text = f"📋 *Sync History — {project['project_name']}*\n\n"
                for log in logs:
                    icon = "✅" if log["status"] == "success" else "❌"
                    ts_raw = log.get("created_at", "")
                    ts = "?"
                    try:
                        dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                        from scheduler import format_ist as fi
                        ts = fi(dt)
                    except Exception:
                        ts = str(ts_raw).replace("T", " ")[:19] if ts_raw else "?"
                    files = log.get("files_changed", 0)
                    commit = log.get("commit_hash", "N/A") or "N/A"
                    text += f"{icon} {ts} — {files} files (`{commit}`)\n"
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=f"select_project:{project_id}")]]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("pushnow:"):
        query = update.callback_query
        await query.answer()
        project_id = int(data.split(":")[1])
        project = db.get_project(project_id)
        if project:
            from handlers.status import do_push
            await do_push(update, project)
    elif data.startswith("pushall:"):
        query = update.callback_query
        await query.answer()
        project_id = int(data.split(":")[1])
        project = db.get_project(project_id)
        if project:
            from handlers.status import do_push_all
            await do_push_all(update, project)
    elif data.startswith("pause:"):
        query = update.callback_query
        await query.answer()
        project_id = int(data.split(":")[1])
        sched = db.get_schedule_by_project(project_id)
        if sched and sched.get("enabled"):
            db.enable_schedule(sched["id"], False)
            scheduler_manager.remove_job(project_id)
            await query.edit_message_text("⏸ Sync paused. Use /resume or /status to resume.")
        else:
            await query.edit_message_text("No active schedule to pause.")
    elif data.startswith("resume:"):
        query = update.callback_query
        await query.answer()
        project_id = int(data.split(":")[1])
        sched = db.get_schedule_by_project(project_id)
        if sched and sched.get("cron_expression"):
            if not sched.get("enabled"):
                db.enable_schedule(sched["id"], True)
                scheduler_manager.add_job(project_id, sched["cron_expression"])
                await query.edit_message_text("▶ Sync resumed!")
            else:
                await query.edit_message_text("Sync already running.")
        else:
            await query.edit_message_text("No schedule set for this project.")
    elif data.startswith("add_account"):
        query = update.callback_query
        await query.answer()
        await query.message.reply_text("Use /addaccount to link a GitHub account.")
    elif data.startswith("del_account:"):
        await delete_account_callback(update, context)
    elif data.startswith("confirm_del_account:"):
        await confirm_delete_account(update, context)
    elif data == "cancel_del_account":
        await cancel_del_account(update, context)
    elif data.startswith("remove_project:"):
        query = update.callback_query
        await query.answer()
        project_id = int(data.split(":")[1])
        project = db.get_project(project_id)
        if project:
            keyboard = [
                [InlineKeyboardButton("✅ Yes, delete", callback_data=f"confirm_remove:{project_id}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"select_project:{project_id}")],
            ]
            await query.edit_message_text(
                f"⚠️ *Delete {project['project_name']}?*\n\n"
                "This will permanently remove the project and all its sync history.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
    elif data.startswith("confirm_remove:"):
        query = update.callback_query
        await query.answer()
        project_id = int(data.split(":")[1])
        project = db.get_project(project_id)
        if project:
            scheduler_manager.remove_job(project_id)
            FileService.cleanup(project["project_path"])
            db.delete_project(project_id)
            await query.edit_message_text(f"🗑 Deleted *{project['project_name']}*.", parse_mode="Markdown")
    elif data.startswith("select_project:"):
        await browse_select_project(update, context)


application: Optional["Application"] = None
_synced_notified: set[int] = set()


def check_network() -> bool:
    logger = logging.getLogger(__name__)
    for host in ("api.telegram.org", "149.154.167.198", "149.154.166.110"):
        try:
            socket.setdefaulttimeout(15)
            socket.create_connection((host, 443))
            logger.info(f"Network OK — connected to {host}:443")
            return True
        except Exception as e:
            logger.debug(f"Connection attempt to {host} failed: {e}")
            continue
    logger.error("Cannot connect to api.telegram.org:443 — check internet/firewall")
    return False


def _start_health_server() -> None:
    from services.upload_server import start_upload_server
    port = int(os.environ.get("PORT", 8080))
    start_upload_server(port)


def main() -> None:
    global application
    setup_logging()
    logger = logging.getLogger(__name__)

    t = threading.Thread(target=_start_health_server, daemon=True)
    t.start()

    if not check_network():
        logger.warning("Starting anyway — will retry connection in background.")

    if not config.BOT_TOKEN:
        logger.critical("BOT_TOKEN not set! Create a .env file with BOT_TOKEN=<your_token>")
        sys.exit(1)

    scheduler_manager.set_sync_callback(scheduled_sync)

    async def _keep_alive() -> None:
        import asyncio
        public_url = os.environ.get("RENDER_EXTERNAL_URL", os.environ.get("PUBLIC_URL", ""))
        retry = 1
        while True:
            await asyncio.sleep(300 if public_url else 60)
            try:
                if public_url:
                    import httpx
                    async with httpx.AsyncClient(timeout=15) as c:
                        r = await c.get(f"{public_url}/health")
                        r.raise_for_status()
                else:
                    port = int(os.environ.get("PORT", 8080))
                    reader, writer = await asyncio.open_connection("localhost", port)
                    writer.close()
                    await writer.wait_closed()
                retry = 1
            except Exception:
                wait = min(retry * 15, 300)
                logger.warning(f"Keep-alive failed, retrying in {wait}s (attempt {retry})")
                await asyncio.sleep(wait)
                retry += 1

    async def post_init(app: Application) -> None:
        scheduler_manager.start()
        scheduler_manager.reschedule_all()
        asyncio.ensure_future(_keep_alive())
        logger.info("Scheduler started and jobs rescheduled.")

    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .connect_timeout(config.CONNECT_TIMEOUT)
        .read_timeout(config.READ_TIMEOUT)
        .write_timeout(config.WRITE_TIMEOUT)
        .pool_timeout(config.CONNECT_TIMEOUT)
        .post_init(post_init)
        .build()
    )

    async def process_web_upload(telegram_id: int, zip_path: str, filename: str) -> None:
        from services.zip_service import ZipService, ZipValidationError
        from analyzer import ProjectAnalyzer
        from services.report_service import ReportService
        import uuid as _uuid
        import os as _os

        logger.info(f"Processing web upload for user {telegram_id}: {filename}")

        try:
            try:
                ZipService.validate(zip_path)
            except ZipValidationError as e:
                try:
                    bot = application.bot
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=f"❌ Validation failed: {e}\n\nTry /upload via Telegram with a valid ZIP file.",
                    )
                except Exception:
                    pass
                ZipService.cleanup(zip_path)
                return

            project_id_hex = _uuid.uuid4().hex
            project_name = _os.path.splitext(filename)[0]
            extract_path = _os.path.join("storage", "projects", project_id_hex)

            try:
                ZipService.extract(zip_path, extract_path)
            except ZipValidationError as e:
                ZipService.cleanup(zip_path)
                ZipService.cleanup(extract_path)
                try:
                    bot = application.bot
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=f"❌ Extraction failed: {e}\n\nTry /upload via Telegram with a valid ZIP file.",
                    )
                except Exception:
                    pass
                return
            finally:
                ZipService.cleanup(zip_path)

            analyzer = ProjectAnalyzer()
            analysis = analyzer.analyze(extract_path)

            user_db = db.get_user_by_telegram_id(telegram_id)
            if not user_db:
                ZipService.cleanup(extract_path)
                return

            project_id = db.add_project(user_db["id"], project_name, extract_path)
            report = ReportService.analysis_report(
                project_name,
                analysis["files"],
                analysis["folders"],
                analysis["loc"],
                analysis["technologies"],
            )

            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = [
                [InlineKeyboardButton("📁 Browse Files", callback_data="browse_root")],
                [InlineKeyboardButton("🔗 Link GitHub", callback_data="link_github")],
                [InlineKeyboardButton("⏰ Set Schedule", callback_data="set_schedule")],
                [InlineKeyboardButton("📋 My Projects", callback_data="list_projects")],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            try:
                bot = application.bot
                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"✅ *Project Uploaded Successfully via Web!*\n\n{report}",
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )
            except Exception as e:
                logger.error(f"Failed to notify user {telegram_id}: {e}")

        except Exception as e:
            logger.exception(f"Web upload processing failed for user {telegram_id}")
            try:
                bot = application.bot
                await bot.send_message(
                    chat_id=telegram_id,
                    text=f"❌ Upload processing failed: {e}\n\nTry /upload via Telegram with a valid ZIP file.",
                )
            except Exception:
                pass

    from services.upload_server import set_upload_processor
    set_upload_processor(process_web_upload)

    # ── Register handlers ──────────────────────────────

    # Basic commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(CommandHandler("menu", main_menu_command))

    # Web upload
    application.add_handler(CommandHandler("webupload", webupload_command))

    # Upload conversation
    upload_conv = ConversationHandler(
        entry_points=[CommandHandler("upload", upload_start)],
        states={
            WAITING_FOR_ZIP: [
                MessageHandler(filters.Document.ALL, receive_zip),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.COMMAND, cancel),
        ],
        allow_reentry=True,
    )
    application.add_handler(upload_conv)

    # GitHub accounts
    application.add_handler(get_add_account_handler())
    application.add_handler(CommandHandler("accounts", accounts_list))
    application.add_handler(CallbackQueryHandler(delete_account_callback, pattern="^del_account:"))
    application.add_handler(CallbackQueryHandler(confirm_delete_account, pattern="^confirm_del_account:"))
    application.add_handler(CallbackQueryHandler(cancel_del_account, pattern="^cancel_del_account"))

    # GitHub link
    application.add_handler(get_github_handler())

    # Schedule
    application.add_handler(get_schedule_handler())

    # Status & push
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("pushnow", pushnow_command))
    application.add_handler(CommandHandler("projects", projects_command))
    from handlers.settings import pause_command, resume_command, batchsize_command
    from handlers.status import pushall_command as pushall_cmd
    application.add_handler(CommandHandler("pause", pause_command))
    application.add_handler(CommandHandler("resume", resume_command))
    application.add_handler(CommandHandler("batchsize", batchsize_command))
    application.add_handler(CommandHandler("pushall", pushall_cmd))

    # Browse
    browse_conv = get_browse_conversation_handler()
    application.add_handler(browse_conv)

    # Admin (filters checked inside handler functions)
    application.add_handler(CommandHandler("users", admin_users))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("logs", admin_logs))
    application.add_handler(CommandHandler("broadcast", admin_broadcast))

    # Global callback router (after more specific ones)
    application.add_handler(CallbackQueryHandler(handle_callback_routing))

    # Catch-all: respond to any unrecognized message
    async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user:
            from database import db
            db.upsert_user(user.id, user.username, user.first_name)
        import html
        text = (
            "I didn't understand that.\n\n"
            "Try these commands:\n"
            "  /start — Show welcome & overview\n"
            "  /upload — Upload a project ZIP\n"
            "  /projects — List your projects\n"
            "  /status — View progress & sync status\n"
            "  /pushnow — Push next batch now\n"
            "  /pause — Pause auto-sync\n"
            "  /resume — Resume auto-sync\n"
            "  /schedule — Change sync frequency\n"
            "  /github — Link a GitHub repository\n"
            "  /accounts — Manage GitHub accounts\n"
            "  /help — Full guide"
        )
        await update.message.reply_text(text)

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message))

    # Error handler — suppress conflict errors from Render deploy overlaps
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = str(context.error)
        if "Conflict" in err and "terminated" in err:
            logger.debug("Bot conflict during deploy overlap — will retry.")
            return
        logger.error(f"Update {update} caused error {context.error}")

    application.add_error_handler(error_handler)

    logger.info("GitSync Bot starting. Polling for updates...")

    time.sleep(5)

    # ── 24/7 Uptime Loop ──────────────────────────────
    for cycle in range(1, 10001):
        try:
            application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
                bootstrap_retries=5,
            )
            logger.info("Polling stopped cleanly.")
            break
        except Exception as e:
            logger.exception(f"Polling cycle {cycle} crashed: {e}")
            try:
                scheduler_manager.stop()
            except Exception:
                pass
            wait = min(30 * cycle, 300)
            logger.info(f"Restarting in {wait}s (cycle {cycle})...")
            time.sleep(wait)


if __name__ == "__main__":
    main()
