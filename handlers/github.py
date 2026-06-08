import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters, CallbackQueryHandler

from database import db
from project_manager import ProjectManager, ProjectManagerError
from github_manager import GitHubManager, GitHubManagerError
from scheduler import scheduler_manager
from services.encryption_service import EncryptionService
from services.report_service import ReportService
from services.file_tracker import FileTracker

logger = logging.getLogger(__name__)

WAITING_REPO_URL = 1
WAITING_ACCOUNT_PICK = 2


async def github_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user:
        await update.message.reply_text("Error: Could not identify user.")
        return ConversationHandler.END

    user_db = db.get_user_by_telegram_id(user.id)
    if not user_db:
        await update.message.reply_text("Please use /start first.")
        return ConversationHandler.END

    projects = db.get_user_projects(user_db["id"])
    if not projects:
        await update.message.reply_text(
            "No projects found. Use /upload first to upload a project."
        )
        return ConversationHandler.END

    if len(projects) == 1:
        context.user_data["link_project_id"] = projects[0]["id"]
    else:
        keyboard = []
        for p in projects:
            status = "✅" if p.get("github_repo") else "⬜"
            keyboard.append([
                InlineKeyboardButton(
                    f"{status} {p['project_name']}",
                    callback_data=f"link_project:{p['id']}",
                )
            ])
        await update.message.reply_text(
            "Select a project to link to GitHub:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return WAITING_ACCOUNT_PICK

    return await ask_account(update, context)


async def ask_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    user_db = db.get_user_by_telegram_id(user.id)
    accounts = db.get_github_accounts(user_db["id"])

    if not accounts:
        await update.message.reply_text(
            "No GitHub accounts found. Use /addaccount first to add one.",
        )
        return ConversationHandler.END

    if len(accounts) == 1:
        context.user_data["link_account_id"] = accounts[0]["id"]
        return await ask_repo_url(update, context)

    keyboard = []
    for a in accounts:
        keyboard.append([
            InlineKeyboardButton(
                f"{a['account_alias']} ({a['github_username']})",
                callback_data=f"link_account:{a['id']}",
            )
        ])
    msg = update.message or update.callback_query.message
    await msg.reply_text(
        "Select a GitHub account:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return WAITING_REPO_URL


async def ask_repo_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message or update.callback_query.message
    await msg.reply_text(
        "Enter the GitHub repository URL:\n\n"
        "Example:\n"
        "`https://github.com/username/repository`\n\n"
        "The repository must already exist on GitHub.\n"
        "Send /cancel to abort.",
        parse_mode="Markdown",
    )
    return WAITING_REPO_URL


async def receive_repo_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = update.message.text.strip()

    if not GitHubManager.validate_repo_url(url):
        await update.message.reply_text(
            "❌ Invalid GitHub URL. Please use:\n"
            "`https://github.com/username/repo`",
            parse_mode="Markdown",
        )
        return WAITING_REPO_URL

    project_id = context.user_data.get("link_project_id")
    account_id = context.user_data.get("link_account_id")

    if not project_id or not account_id:
        await update.message.reply_text("Error: Missing project or account info. Start again with /github.")
        return ConversationHandler.END

    project = db.get_project(project_id)
    account = db.get_github_account(account_id)

    if not project or not account:
        await update.message.reply_text("Error: Project or account not found.")
        return ConversationHandler.END

    token = EncryptionService.decrypt(account["token_encrypted"])

    status_msg = await update.message.reply_text("🔄 Initializing git repository and pushing to GitHub...")

    try:
        result = GitHubManager.init_repo(
            project["project_path"],
            url,
            token,
        )

        ProjectManager.link_github(project_id, account_id, url)

        DEFAULT_CRON = "0 */4 * * *"
        ProjectManager.set_schedule(project_id, DEFAULT_CRON)
        scheduler_manager.add_job(project_id, DEFAULT_CRON)

        progress = FileTracker.get_progress(project["project_path"], project_id)

        await status_msg.edit_text(
            f"✅ *GitHub Integration Successful!*\n\n"
            f"Repository: `{url}`\n"
            f"Branch: `{result['branch']}`\n"
            f"Total files to upload: {progress['total']}\n\n"
            f"⏰ *Auto-sync started!*\n"
            f"Pushing files in batches of 4 every 4 hours.\n"
            f"Progress: 0/{progress['total']} files\n\n"
            f"📬 You will receive an update after each batch.\n"
            f"Use /status to check progress anytime.\n"
            f"Use /schedule to change the frequency.",
            parse_mode="Markdown",
        )

    except GitHubManagerError as e:
        await status_msg.edit_text(f"❌ GitHub setup failed: {e}")
    except Exception as e:
        logger.exception("GitHub init error")
        await status_msg.edit_text(f"❌ Unexpected error: {e}")

    context.user_data.pop("link_project_id", None)
    context.user_data.pop("link_account_id", None)
    return ConversationHandler.END


async def pick_project_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("link_project:"):
        project_id = int(data.split(":")[1])
        context.user_data["link_project_id"] = project_id

    return await ask_account(update, context)


async def pick_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("link_account:"):
        account_id = int(data.split(":")[1])
        context.user_data["link_account_id"] = account_id

    return await ask_repo_url(update, context)


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def get_github_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("github", github_start)],
        states={
            WAITING_ACCOUNT_PICK: [
                CallbackQueryHandler(pick_project_callback, pattern="^link_project:"),
            ],
            WAITING_REPO_URL: [
                CallbackQueryHandler(pick_account_callback, pattern="^link_account:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_repo_url),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conversation),
            MessageHandler(filters.COMMAND, cancel_conversation),
        ],
        allow_reentry=True,
        per_user=True,
    )
