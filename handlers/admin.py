import os
import logging
from telegram import Update, InputFile
from telegram.ext import ContextTypes, CommandHandler, filters

from database import db
from config import config

logger = logging.getLogger(__name__)


async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update):
        return
    users = db.get_all_users()
    text = f"👥 *Users ({len(users)})*\n\n"
    for u in users:
        text += f"  `{u.get('telegram_id', '?')}` — {u.get('first_name') or u.get('username') or 'Unknown'}\n"
        if len(text) > 3500:
            text += "... (truncated)"
            break
    await update.message.reply_text(text, parse_mode="Markdown")


async def admin_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update):
        return
    projects = db.get_all_projects()
    text = f"📦 *All Projects ({len(projects)})*\n\n"
    for p in projects:
        user = db.get_user(p.get("user_id", 0))
        username = (user.get("username") or user.get("first_name") or f"ID:{p.get('user_id', 0)}") if user else "Unknown"
        linked = "🔗" if p.get("github_repo") else "📁"
        text += f"  {linked} `{p.get('project_name', '?')}` — {username}\n"
        if len(text) > 3500:
            text += "... (truncated)"
            break
    await update.message.reply_text(text, parse_mode="Markdown")


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update):
        return
    stats = db.get_stats()
    total_syncs = stats.get("total_syncs", 0)
    failed_syncs = stats.get("failed_syncs", 0)
    success_rate = ((total_syncs - failed_syncs) / max(total_syncs, 1) * 100)
    text = (
        f"📊 *Bot Statistics*\n\n"
        f"👤 Users: {stats.get('total_users', 0)}\n"
        f"📦 Projects: {stats.get('total_projects', 0)}\n"
        f"🔄 Total Syncs: {total_syncs}\n"
        f"❌ Failed Syncs: {failed_syncs}\n"
        f"✅ Success Rate: {success_rate:.1f}%"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /broadcast <message>\n\n"
            "Sends a message to every user of the bot."
        )
        return

    message = " ".join(context.args)
    users = db.get_all_users()
    sent = 0
    failed = 0

    status_msg = await update.message.reply_text(f"📨 Broadcasting to {len(users)} users...")

    for u in users:
        try:
            tid = u.get("telegram_id")
            if not tid:
                failed += 1
                continue
            await context.bot.send_message(
                chat_id=tid,
                text=f"📢 *Bot Announcement*\n\n{message}",
                parse_mode="Markdown",
            )
            sent += 1
        except Exception as e:
            failed += 1
            logger.warning(f"Broadcast failed to {u.get('telegram_id', '?')}: {e}")

    await status_msg.edit_text(
        f"📨 *Broadcast Complete*\n\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {failed}\n"
        f"👥 Total: {len(users)}",
        parse_mode="Markdown",
    )


async def admin_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update):
        return
    log_path = os.path.join(os.getcwd(), "storage", "logs", "bot.log")
    if not os.path.exists(log_path):
        await update.message.reply_text("No log file found.")
        return

    try:
        with open(log_path, "rb") as f:
            data = f.read()
        await update.message.reply_document(
            document=InputFile(data, filename="bot.log"),
            caption="📋 Bot Log File",
        )
    except Exception as e:
        await update.message.reply_text(f"Error sending log: {e}")


async def is_admin(update: Update) -> bool:
    user = update.effective_user
    if not user:
        await update.message.reply_text("Could not identify user.")
        return False
    if not config.ADMIN_IDS or user.id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized. This command is for admins only.")
        return False
    return True
