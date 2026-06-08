from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import db


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    is_new = db.get_user_by_telegram_id(user.id) is None
    db.upsert_user(user.id, user.username, user.first_name)

    msg = (
        f"👋 Welcome *{user.first_name}*!\n\n"
        "🤖 I automatically upload your project files to GitHub in batches.\n\n"
        "📌 *Quick Start (4 steps):*\n"
        "1️⃣ */addaccount* — Store your GitHub token\n"
        "2️⃣ */upload* — Send your project ZIP\n"
        "3️⃣ */github* — Link to a GitHub repo\n"
        "4️⃣ ✅ *Auto-sync begins!*\n\n"
        "⏰ *Sync Schedule:* Every 4 hours by default (change anytime)\n"
        "📤 *Batch Size:* 4 files per push (change via /batchsize)\n\n"
        "📊 /status — Track sync progress\n"
        "⏸ /pause · /resume — Pause or resume syncing\n"
        "📖 /help — All commands\n\n"
        "🔒 Your data is private and secure."
    )
    if not is_new:
        msg = (
            f"🤖 *GitSync Bot*\n\n"
            "I sync your project files to GitHub automatically.\n\n"
            "*Quick commands:*\n"
            "  /upload — Upload a project ZIP\n"
            "  /projects — List your projects\n"
            "  /status — Progress & sync status\n"
            "  /pushnow — Push next batch now\n"
            "  /pause · /resume — Control auto-sync\n"
            "  /schedule — Change sync frequency\n"
            "  /batchsize — Files per batch (1-50)\n"
            "  /github — Link a repository\n"
            "  /accounts — Manage GitHub accounts\n"
            "  /menu — Interactive menu\n"
            "  /help — Full guide"
        )

    keyboard = [
        [InlineKeyboardButton("📤 Upload Project", callback_data="upload")],
        [InlineKeyboardButton("📖 Quick Guide", callback_data="show_help")],
    ]
    await update.message.reply_text(
        msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("📤 Upload", callback_data="upload")],
        [InlineKeyboardButton("📋 My Projects", callback_data="list_projects")],
        [InlineKeyboardButton("🔗 Link GitHub", callback_data="link_github")],
        [InlineKeyboardButton("⏰ Schedule", callback_data="set_schedule")],
    ]
    await update.message.reply_text(
        "*GitSync Bot — Full Guide*\n\n"
        "*1. Add a GitHub Account*\n"
        "  /addaccount — Store your GitHub Personal Access Token.\n"
        "  Get one at GitHub Settings → Developer settings → Tokens.\n\n"
        "*2. Upload a Project*\n"
        "  /upload — Send a ZIP file. Bot extracts and analyzes it.\n"
        "  After upload you can browse, delete, rename files.\n\n"
        "*3. Link to GitHub*\n"
        "  /github — Connect a project to a repository.\n"
        "  Auto-sync starts immediately (4 files every 4 hours).\n\n"
        "*4. Control Sync*\n"
            "  /schedule — Choose frequency (Every 4h, hourly, daily, custom)\n"
            "  /pause — Stop auto-sync temporarily\n"
            "  /resume — Restart auto-sync\n"
            "  /pushnow — Push next batch right now\n"
            "  /batchsize — Set files per batch (1-50, default 4)\n\n"
        "*5. Monitor*\n"
        "  /status — Files pushed, remaining, next sync time\n"
        "  /projects — List all your projects\n\n"
        "*6. File Management*\n"
        "  After uploading, tap \"Browse Files\" to view, delete, rename.\n\n"
        "🔒 *Privacy:* Your projects are private to you.\n"
        "Admins can see usage stats but not your files.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 *GitSync Bot*\n\n"
        "Batch upload your projects to GitHub with auto-sync.\n"
        "Pushes 4 files at a time on a schedule you choose.\n\n"
        "✨ Features:\n"
        "  • Auto-sync every 4 hours (default)\n"
        "  • Pause and resume anytime\n"
        "  • Multiple GitHub accounts\n"
        "  • Per-user private data\n"
        "  • File browser & management\n\n"
        "Free & Open Source",
        parse_mode="Markdown",
    )
