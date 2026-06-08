import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from database import db
from project_manager import ProjectManager
from services.file_service import FileService, FileServiceError
from services.report_service import ReportService

logger = logging.getLogger(__name__)

WAITING_RENAME = 1
WAITING_CREATE_FILE = 2
WAITING_CREATE_FOLDER = 3
WAITING_FILE_CONTENT = 4

MAX_ITEMS_PER_PAGE = 20


def get_project_path(project_id: int) -> str:
    project = db.get_project(project_id)
    if not project:
        raise FileServiceError("Project not found.")
    return project["project_path"]


async def browse_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "browse_root":
        project_id = context.user_data.get("current_project_id")
        if not project_id:
            await query.edit_message_text("No project selected. Use /upload first.")
            return
        try:
            project_path = get_project_path(project_id)
            await show_directory(query, context, project_path)
        except FileServiceError as e:
            await query.edit_message_text(str(e))
    elif data.startswith("browse_dir:"):
        path = data[len("browse_dir:"):]
        await show_directory(query, context, path)
    elif data.startswith("view_file:"):
        path = data[len("view_file:"):]
        await show_file(query, context, path)
    elif data.startswith("delete_path:"):
        path = data[len("delete_path:"):]
        await confirm_delete(query, context, path)
    elif data.startswith("confirm_delete:"):
        path = data[len("confirm_delete:"):]
        await perform_delete(query, context, path)
    elif data.startswith("rename_start:"):
        path = data[len("rename_start:"):]
        context.user_data["rename_path"] = path
        await query.edit_message_text(
            f"✏️ Enter new name for:\n`{os.path.basename(path)}`\n\n"
            "Send /cancel to abort.",
            parse_mode="Markdown",
        )
        return WAITING_RENAME
    elif data.startswith("create_file:"):
        path = data[len("create_file:"):]
        context.user_data["create_dir"] = path
        await query.edit_message_text(
            "📄 Enter the file name (e.g., `newfile.py`):",
            parse_mode="Markdown",
        )
        return WAITING_CREATE_FILE
    elif data.startswith("create_folder:"):
        path = data[len("create_folder:"):]
        context.user_data["create_dir"] = path
        await query.edit_message_text(
            "📁 Enter the folder name:",
        )
        return WAITING_CREATE_FOLDER
    elif data == "list_projects":
        await list_projects(query, context)

    return ConversationHandler.END


async def show_directory(
    query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, path: str
) -> None:
    try:
        listing = FileService.list_dir(path, max_items=MAX_ITEMS_PER_PAGE)
    except FileServiceError as e:
        await query.edit_message_text(str(e))
        return

    keyboard = []
    for d in listing["dirs"]:
        keyboard.append(
            [InlineKeyboardButton(f"📁 {d['name']}", callback_data=f"browse_dir:{os.path.join(path, d['name'])}")]
        )
    for f in listing["files"]:
        fpath = os.path.join(path, f["name"])
        size_kb = f["size"] / 1024
        label = f"📄 {f['name']} ({size_kb:.1f} KB)" if size_kb < 1024 else f"📄 {f['name']} ({size_kb/1024:.1f} MB)"
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=f"view_file:{fpath}")]
        )

    parent = FileService.get_parent(path)
    nav_buttons = []
    if parent != path:
        nav_buttons.append(InlineKeyboardButton("⬆️ Back", callback_data=f"browse_dir:{parent}"))
    if listing["dirs"] or listing["files"]:
        nav_buttons.append(InlineKeyboardButton("🗑 Delete", callback_data=f"delete_path:{path}"))
    elif parent != path:
        nav_buttons.append(InlineKeyboardButton("🗑 Delete Folder", callback_data=f"delete_path:{path}"))
    nav_buttons.append(InlineKeyboardButton("📄 +File", callback_data=f"create_file:{path}"))
    nav_buttons.append(InlineKeyboardButton("📁 +Folder", callback_data=f"create_folder:{path}"))

    if len(nav_buttons) <= 3:
        keyboard.append(nav_buttons)
    else:
        keyboard.append(nav_buttons[:3])
        keyboard.append(nav_buttons[3:])

    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])

    title = f"📁 *{os.path.basename(path) or 'Root'}*"
    if listing["truncated"]:
        title += f"\n_Showing {len(listing['dirs']) + len(listing['files'])} of {listing['total']} items_"

    await query.edit_message_text(
        title,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_file(
    query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, path: str
) -> None:
    try:
        content = FileService.read_file(path)
    except FileServiceError as e:
        await query.edit_message_text(str(e))
        return

    basename = os.path.basename(path)
    keyboard = [
        [
            InlineKeyboardButton("⬆️ Back", callback_data=f"browse_dir:{FileService.get_parent(path)}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"delete_path:{path}"),
        ],
        [
            InlineKeyboardButton("✏️ Rename", callback_data=f"rename_start:{path}"),
        ],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")],
    ]

    text = f"📄 *{basename}*\n\n```\n{content}\n```"
    if len(text) > 4096:
        text = f"📄 *{basename}*\n\n_File too large to display fully._\n\n```\n{content[:3500]}\n```"

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def confirm_delete(
    query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, path: str
) -> None:
    basename = os.path.basename(path) or path
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, delete", callback_data=f"confirm_delete:{path}"),
            InlineKeyboardButton("❌ No", callback_data=f"browse_dir:{FileService.get_parent(path)}"),
        ]
    ]
    await query.edit_message_text(
        f"🗑 Are you sure you want to delete `{basename}`?\n\nThis cannot be undone.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def perform_delete(
    query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, path: str
) -> None:
    try:
        FileService.delete(path)
        parent = FileService.get_parent(path)
        await query.edit_message_text(
            f"✅ Deleted successfully.",
        )
        await show_directory(query, context, parent)
    except FileServiceError as e:
        await query.edit_message_text(f"❌ Delete failed: {e}")


async def handle_rename_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_name = update.message.text.strip()
    old_path = context.user_data.get("rename_path")

    if not old_path:
        await update.message.reply_text("Error: No file selected for rename.")
        return ConversationHandler.END

    try:
        FileService.rename(old_path, new_name)
        parent = FileService.get_parent(old_path)
        await update.message.reply_text(f"✅ Renamed to `{new_name}`", parse_mode="Markdown")
        listing = FileService.list_dir(parent)
        keyboard = build_dir_keyboard(listing, parent)
        await update.message.reply_text(
            f"📁 *{os.path.basename(parent)}*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except FileServiceError as e:
        await update.message.reply_text(f"❌ Rename failed: {e}")

    return ConversationHandler.END


async def handle_create_file_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    create_dir = context.user_data.get("create_dir")

    if not name or not create_dir:
        await update.message.reply_text("Error: Missing information.")
        return ConversationHandler.END

    path = os.path.join(create_dir, name)
    try:
        FileService.create_file(path, "")
        await update.message.reply_text(f"✅ File created: `{name}`", parse_mode="Markdown")
        listing = FileService.list_dir(create_dir)
        keyboard = build_dir_keyboard(listing, create_dir)
        await update.message.reply_text(
            f"📁 *{os.path.basename(create_dir)}*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except FileServiceError as e:
        await update.message.reply_text(f"❌ Create failed: {e}")

    return ConversationHandler.END


async def handle_create_folder_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    create_dir = context.user_data.get("create_dir")

    if not name or not create_dir:
        await update.message.reply_text("Error: Missing information.")
        return ConversationHandler.END

    path = os.path.join(create_dir, name)
    try:
        FileService.create_folder(path)
        await update.message.reply_text(f"✅ Folder created: `{name}`", parse_mode="Markdown")
        listing = FileService.list_dir(create_dir)
        keyboard = build_dir_keyboard(listing, create_dir)
        await update.message.reply_text(
            f"📁 *{os.path.basename(create_dir)}*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except FileServiceError as e:
        await update.message.reply_text(f"❌ Create failed: {e}")

    return ConversationHandler.END


def build_dir_keyboard(listing: dict, path: str) -> list:
    keyboard = []
    for d in listing["dirs"]:
        keyboard.append(
            [InlineKeyboardButton(f"📁 {d['name']}", callback_data=f"browse_dir:{os.path.join(path, d['name'])}")]
        )
    for f in listing["files"]:
        fpath = os.path.join(path, f["name"])
        size_kb = f["size"] / 1024
        label = f"📄 {f['name']} ({size_kb:.1f} KB)"
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=f"view_file:{fpath}")]
        )
    parent = FileService.get_parent(path)
    keyboard.append([
        InlineKeyboardButton("⬆️ Back", callback_data=f"browse_dir:{parent}"),
        InlineKeyboardButton("📄 +File", callback_data=f"create_file:{path}"),
    ])
    keyboard.append([
        InlineKeyboardButton("🗑 Delete", callback_data=f"delete_path:{path}" if listing["dirs"] or listing["files"] else "noop"),
        InlineKeyboardButton("📁 +Folder", callback_data=f"create_folder:{path}"),
    ])
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
    return keyboard


async def list_projects(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = query.from_user
    user_db = db.get_user_by_telegram_id(user.id)
    if not user_db:
        await query.edit_message_text("No user found. Use /start first.")
        return

    projects = ProjectManager.get_user_projects(user_db["id"])
    if not projects:
        await query.edit_message_text("No projects found. Use /upload to add one.")
        return

    text = "*Your Projects*\n\n"
    keyboard = []
    for p in projects:
        status = "🔗" if p.get("github_repo") else "📁"
        text += f"{status} `{p['project_name']}` (ID: {p['id']})\n"
        keyboard.append([
            InlineKeyboardButton(
                f"📂 {p['project_name']}",
                callback_data=f"select_project:{p['id']}",
            )
        ])

    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def select_project_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("select_project:"):
        project_id = int(data.split(":")[1])
        context.user_data["current_project_id"] = project_id

        project = db.get_project(project_id)
        if not project:
            await query.edit_message_text("Project not found.")
            return

        keyboard = [
            [InlineKeyboardButton("📁 Browse Files", callback_data="browse_root")],
            [InlineKeyboardButton("🔗 Link GitHub", callback_data="link_github")],
            [InlineKeyboardButton("⏰ Set Schedule", callback_data="set_schedule")],
            [InlineKeyboardButton("📊 Status", callback_data="show_status")],
            [InlineKeyboardButton("🗑 Remove Project", callback_data=f"remove_project:{project_id}")],
            [InlineKeyboardButton("🔙 Projects", callback_data="list_projects")],
        ]
        await query.edit_message_text(
            f"📂 *{project['project_name']}*\n\n"
            f"Repository: {project['github_repo'] or 'Not linked'}\n"
            f"Schedule: {project['schedule_time'] or 'Not set'}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


def get_browse_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(browse_callback, pattern="^(browse_|view_file|delete_path|confirm_delete|rename_start|create_file|create_folder|list_projects)")],
        states={
            WAITING_RENAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rename_text)],
            WAITING_CREATE_FILE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_create_file_text)],
            WAITING_CREATE_FOLDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_create_folder_text)],
        },
        fallbacks=[MessageHandler(filters.COMMAND, lambda u, c: ConversationHandler.END)],
        allow_reentry=True,
        per_user=True,
        name="browse_conversation",
    )
