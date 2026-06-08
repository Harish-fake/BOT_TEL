import os
import logging
from telegram import Update
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
        text += f"  `{u['telegram_id']}` — {u['first_name'] or u['username'] or 'Unknown'}\n"
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
        user = db.get_user(p["user_id"])
        username = (user.get("username") or user.get("first_name") or f"ID:{p['user_id']}") if user else "Unknown"
        linked = "🔗" if p.get("github_repo") else "📁"
        text += f"  {linked} `{p['project_name']}` — {username}\n"
        if len(text) > 3500:
            text += "... (truncated)"
            break
    await update.message.reply_text(text, parse_mode="Markdown")


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update):
        return
    stats = db.get_stats()
    success_rate = (
        ((stats["total_syncs"] - stats["failed_syncs"]) / max(stats["total_syncs"], 1) * 100)
    )
    text = (
        f"📊 *Bot Statistics*\n\n"
        f"👤 Users: {stats['total_users']}\n"
        f"📦 Projects: {stats['total_projects']}\n"
        f"🔄 Total Syncs: {stats['total_syncs']}\n"
        f"❌ Failed Syncs: {stats['failed_syncs']}\n"
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
            await context.bot.send_message(
                chat_id=u["telegram_id"],
                text=f"📢 *Bot Announcement*\n\n{message}",
                parse_mode="Markdown",
            )
            sent += 1
        except Exception as e:
            failed += 1
            logger.warning(f"Broadcast failed to {u['telegram_id']}: {e}")

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
    log_path = os.path.join("storage", "logs", "bot.log")
    if not os.path.exists(log_path):
        await update.message.reply_text("No log file found.")
        return

    try:
        with open(log_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename="bot.log",
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
