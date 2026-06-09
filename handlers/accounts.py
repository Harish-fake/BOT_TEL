import logging
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters

from database import db
from services.report_service import ReportService
from services.encryption_service import EncryptionService

logger = logging.getLogger(__name__)

WAITING_ALIAS = 1
WAITING_USERNAME = 2
WAITING_TOKEN = 3
WAITING_DELETE_CONFIRM = 4


async def accounts_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    user_db = db.get_user_by_telegram_id(user.id)
    if not user_db:
        await update.message.reply_text("Please use /start first.")
        return

    accounts = db.get_github_accounts(user_db["id"])
    if not accounts:
        keyboard = [[InlineKeyboardButton("➕ Add Account", callback_data="add_account")]]
        await update.message.reply_text(
            "No GitHub accounts linked.\n\nUse /addaccount to link one.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    text = "*Linked GitHub Accounts*\n\n"
    keyboard = []
    for a in accounts:
        text += f"  `{a['account_alias']}` — {a['github_username']}\n"
        keyboard.append([
            InlineKeyboardButton(
                f"🗑 Remove {a['account_alias']}",
                callback_data=f"del_account:{a['id']}",
            )
        ])
    keyboard.append([InlineKeyboardButton("➕ Add Account", callback_data="add_account")])

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "➕ *Add GitHub Account*\n\n"
        "Enter a name for this account (e.g., \"Personal\", \"Work\"):\n\n"
        "Send /cancel to abort.",
        parse_mode="Markdown",
    )
    return WAITING_ALIAS


async def receive_alias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    alias = update.message.text.strip()
    if len(alias) < 1 or len(alias) > 50:
        await update.message.reply_text("Alias must be 1-50 characters.")
        return WAITING_ALIAS

    context.user_data["account_alias"] = alias
    await update.message.reply_text(
        f"Great! Now enter your GitHub *username*:",
        parse_mode="Markdown",
    )
    return WAITING_USERNAME


async def receive_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    username = update.message.text.strip()
    if not username:
        await update.message.reply_text("Username cannot be empty.")
        return WAITING_USERNAME

    context.user_data["github_username"] = username
    await update.message.reply_text(
        "Now enter your GitHub *Personal Access Token* (PAT).\n\n"
        "It will be encrypted and stored securely.\n\n"
        "Create one at: https://github.com/settings/tokens\n"
        "Required scopes: `repo` (for private repos), `public_repo`",
        parse_mode="Markdown",
    )
    return WAITING_TOKEN


async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    token = update.message.text.strip()
    if not token:
        await update.message.reply_text("Token cannot be empty.")
        return WAITING_TOKEN

    alias = context.user_data.get("account_alias", "GitHub")
    gh_username = context.user_data.get("github_username", "")

    user = update.effective_user
    user_db = db.get_user_by_telegram_id(user.id)
    if not user_db:
        await update.message.reply_text("Error: User not found.")
        return ConversationHandler.END

    encrypted = EncryptionService.encrypt(token)
    db.add_github_account(user_db["id"], alias, gh_username, encrypted)

    await update.message.reply_text(
        f"✅ GitHub account *{alias}* ({gh_username}) added successfully!\n\n"
        "You can now use /github to link it to a project.",
        parse_mode="Markdown",
    )

    context.user_data.pop("account_alias", None)
    context.user_data.pop("github_username", None)
    return ConversationHandler.END


async def delete_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("del_account:"):
        account_id = int(data.split(":")[1])
        account = db.get_github_account(account_id)
        if not account:
            await query.edit_message_text("Account not found.")
            return

        keyboard = [
            [
                InlineKeyboardButton("✅ Yes, remove", callback_data=f"confirm_del_account:{account_id}"),
                InlineKeyboardButton("❌ No", callback_data="cancel_del_account"),
            ]
        ]
        await query.edit_message_text(
            f"🗑 Remove GitHub account *{account['account_alias']}* ({account['github_username']})?\n\n"
            "Projects linked to this account will be unlinked.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def confirm_delete_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("confirm_del_account:"):
        account_id = int(data.split(":")[1])
        user = query.from_user
        user_db = db.get_user_by_telegram_id(user.id)
        if user_db:
            db.delete_github_account(account_id, user_db["id"])

        await query.edit_message_text("✅ Account removed.", reply_markup=None)


async def cancel_del_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Cancelled.", reply_markup=None)


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def get_add_account_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("addaccount", add_account_start)],
        states={
            WAITING_ALIAS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_alias)],
            WAITING_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_username)],
            WAITING_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conversation),
            MessageHandler(filters.COMMAND, cancel_conversation),
        ],
        allow_reentry=True,
    )
