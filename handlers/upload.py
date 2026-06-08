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

PROJECTS_DIR = os.path.join("storage", "projects")


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
        file = await document.get_file()
        file_bytes = await file.download_as_bytearray()
    except Exception as e:
        await status_msg.edit_text(f"❌ Download failed: {e}")
        return ConversationHandler.END

    zip_path = ZipService.save_temp_file(bytes(file_bytes), document.file_name)

    try:
        await status_msg.edit_text("🔍 Validating ZIP...")
        ZipService.validate(zip_path)
    except ZipValidationError as e:
        ZipService.cleanup(zip_path)
        await status_msg.edit_text(f"❌ Validation failed: {e}")
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
        await status_msg.edit_text(f"❌ Extraction failed: {e}")
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
        analysis["files"],
        analysis["folders"],
        analysis["loc"],
        analysis["technologies"],
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
