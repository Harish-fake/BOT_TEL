import os
import uuid
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from config import config
from database import db
from services.zip_service import ZipService, ZipValidationError
from analyzer import ProjectAnalyzer
from services.report_service import ReportService

logger = logging.getLogger(__name__)

WAITING_FOR_ZIP = 1

PROJECTS_DIR = os.path.join(os.getcwd(), "storage", "projects")


async def upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if user:
        db.upsert_user(user.id, user.username, user.first_name)

    await update.message.reply_text(
        "📤 *Upload Project*\n\n"
        "Please send a ZIP file of your project.\n"
        f"Maximum size: {config.MAX_FILE_SIZE_MB} MB.\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown",
    )
    return WAITING_FOR_ZIP


async def receive_zip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user:
        await update.message.reply_text("Error: Could not identify user.")
        return ConversationHandler.END

    document = update.message.document
    if not document:
        await update.message.reply_text("Please send a ZIP file.")
        return WAITING_FOR_ZIP

    if not document.file_name or not document.file_name.lower().endswith(".zip"):
        await update.message.reply_text("Only ZIP files are accepted. Please send a .zip file.")
        return WAITING_FOR_ZIP

    status_msg = await update.message.reply_text("⬇️ Downloading ZIP file...")

    try:
        file = await document.get_file(read_timeout=300)
        size_mb = (file.file_size or 0) / (1024 * 1024)

        if file.file_size and file.file_size > 50 * 1024 * 1024:
            await status_msg.edit_text(
                f"❌ File too large ({size_mb:.1f} MB). Telegram's limit is 50 MB.\n"
                "Use /webupload to upload files up to 2 GB via browser."
            )
            return ConversationHandler.END

        temp_dir = os.path.join("storage", "temp")
        os.makedirs(temp_dir, exist_ok=True)
        zip_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}_{document.file_name}")

        bot_token = config.BOT_TOKEN
        dl_url = f"https://api.telegram.org/file/bot{bot_token}/{file.file_path}"

        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=60.0)) as client:
            async with client.stream("GET", dl_url) as resp:
                if resp.status_code in (413, 502):
                    raise RuntimeError("413 File too large")
                resp.raise_for_status()
                content_len = resp.headers.get("content-length")
                if content_len and int(content_len) > 50 * 1024 * 1024:
                    raise RuntimeError(f"413 File too large ({int(content_len)/1024/1024:.0f} MB)")
                with open(zip_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
    except Exception as e:
        err = str(e).lower()
        if "too big" in err or "413" in err:
            await status_msg.edit_text(
                "❌ File too large. Telegram's bot API has a 50 MB download limit.\n"
                "Use /webupload to upload files up to 2 GB via browser."
            )
        else:
            await status_msg.edit_text(
                f"❌ Download failed: {err}\n\n"
                "Try /webupload to upload files up to 2 GB via browser."
            )
        return ConversationHandler.END

    try:
        await status_msg.edit_text("🔍 Validating ZIP...")
        ZipService.validate(zip_path)
    except ZipValidationError as e:
        ZipService.cleanup(zip_path)
        await status_msg.edit_text(
            f"❌ Validation failed: {e}\n\n"
            "Use /webupload to upload files up to 2 GB via browser."
        )
        return ConversationHandler.END

    project_id_hex = uuid.uuid4().hex
    project_name = os.path.splitext(document.file_name)[0]
    extract_path = os.path.join(PROJECTS_DIR, project_id_hex)

    try:
        await status_msg.edit_text("📂 Extracting files...")
        ZipService.extract(zip_path, extract_path)
    except ZipValidationError as e:
        ZipService.cleanup(zip_path)
        ZipService.cleanup(extract_path)
        await status_msg.edit_text(
            f"❌ Extraction failed: {e}\n\n"
            "Use /webupload to upload files up to 2 GB via browser."
        )
        return ConversationHandler.END
    finally:
        ZipService.cleanup(zip_path)

    await status_msg.edit_text("🔬 Analyzing project...")
    analyzer = ProjectAnalyzer()
    analysis = analyzer.analyze(extract_path)

    user_db = db.get_user_by_telegram_id(user.id)
    if not user_db:
        await status_msg.edit_text("❌ User not found. Try /start first.")
        ZipService.cleanup(extract_path)
        return ConversationHandler.END

    project_id = db.add_project(user_db["id"], project_name, extract_path)
    context.user_data["current_project_id"] = project_id

    report = ReportService.analysis_report(
        project_name,
        analysis.get("files", 0),
        analysis.get("folders", 0),
        analysis.get("loc", 0),
        analysis.get("technologies", []),
    )

    keyboard = [
        [InlineKeyboardButton("📁 Browse Files", callback_data="browse_root")],
        [InlineKeyboardButton("🔗 Link GitHub", callback_data="link_github")],
        [InlineKeyboardButton("⏰ Set Schedule", callback_data="set_schedule")],
        [InlineKeyboardButton("📋 My Projects", callback_data="list_projects")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await status_msg.edit_text(
        f"✅ *Project Uploaded Successfully!*\n\n{report}",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Upload cancelled.")
    return ConversationHandler.END
