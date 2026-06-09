import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import config
from services.upload_server import create_session

logger = logging.getLogger(__name__)


async def webupload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        await update.message.reply_text("Error: Could not identify user.")
        return

    token = create_session(user.id)
    public_url = os.environ.get("RENDER_EXTERNAL_URL", os.environ.get("PUBLIC_URL", ""))
    upload_url = f"{public_url}/upload/{token}"

    keyboard = [
        [InlineKeyboardButton("📤 Open Upload Page", url=upload_url)],
    ]

    await update.message.reply_text(
        "📤 *Web Upload*\n\n"
        "Click the button below to open the upload page in your browser.\n"
        "You can upload ZIP files larger than 50 MB there.\n\n"
        f"⏳ This link expires in 1 hour.\n"
        f"📦 Max file size: 2 GB.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
