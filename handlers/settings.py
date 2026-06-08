import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from apscheduler.triggers.cron import CronTrigger
from database import db
from project_manager import ProjectManager
from scheduler import scheduler_manager
from services.schedule_service import ScheduleService
from services.report_service import ReportService

logger = logging.getLogger(__name__)

SELECTING_PROJECT = 1
SELECTING_SCHEDULE = 2
SELECTING_CUSTOM_CRON = 3


SCHEDULE_OPTIONS: list[tuple[str, str, str]] = [
    ("⏰ Every 4 Hours (from now)", "interval:4", "every_4h"),
    ("⚡ Every Hour (from now)", "interval:1", "hourly"),
    ("🕐 Every 6 Hours (from now)", "interval:6", "every_6h"),
    ("🕑 Every 12 Hours (from now)", "interval:12", "every_12h"),
    ("🌅 Daily at 6:30 AM IST", "30 6 * * *", "daily_630am"),
    ("🌞 Daily at 9:30 AM IST", "30 9 * * *", "daily_930am"),
    ("☀️ Daily at 12:00 PM IST", "0 12 * * *", "daily_12pm"),
    ("🌆 Daily at 6:30 PM IST", "30 18 * * *", "daily_630pm"),
    ("🌙 Daily at 9:30 PM IST", "30 21 * * *", "daily_930pm"),
    ("📅 Every Monday 9:30 AM IST", "30 9 * * 1", "weekly_monday"),
    ("📅 Every Wednesday 9:30 AM IST", "30 9 * * 3", "weekly_wednesday"),
    ("📅 Every Friday 9:30 AM IST", "30 9 * * 5", "weekly_friday"),
    ("🔧 Custom (crontab)", "custom", "custom"),
]

MENU_BUTTON = [InlineKeyboardButton("🔙 Back", callback_data="sched_back_menu")]


async def schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
        await update.message.reply_text("No projects found. Use /upload first.")
        return ConversationHandler.END

    keyboard = []
    for p in projects:
        sched = db.get_schedule_by_project(p["id"])
        indicator = "⏰" if sched else "⬜"
        keyboard.append([
            InlineKeyboardButton(
                f"{indicator} {p['project_name']}",
                callback_data=f"sched_project:{p['id']}",
            )
        ])

    await update.message.reply_text(
        "⏰ *Set Schedule*\n\nSelect a project:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SELECTING_PROJECT


async def select_project_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("sched_project:"):
        project_id = int(data.split(":")[1])
        context.user_data["sched_project_id"] = project_id
        project = db.get_project(project_id)

        schedule = db.get_schedule_by_project(project_id)
        current = ""
        paused = False
        if schedule:
            cron = schedule.get("cron_expression")
            paused = not schedule.get("enabled", 1)
            desc = ScheduleService.describe_cron(cron)
            status = "⏸ Paused" if paused else "⏰ Active"
            current = f"\nCurrent: {desc} ({status})"

        keyboard = []
        for label, cron, key in SCHEDULE_OPTIONS:
            keyboard.append([
                InlineKeyboardButton(label, callback_data=f"sched_option:{key}:{cron}")
            ])
        controls = []
        if schedule:
            if paused:
                controls.append(InlineKeyboardButton("▶ Resume", callback_data="sched_resume"))
            else:
                controls.append(InlineKeyboardButton("⏸ Pause", callback_data="sched_pause"))
        controls.append(InlineKeyboardButton("❌ Remove", callback_data="sched_remove"))
        keyboard.append(controls)

        await query.edit_message_text(
            f"⏰ *Schedule — {project['project_name']}*{current}\n\nSelect frequency:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return SELECTING_SCHEDULE

    return ConversationHandler.END


async def select_schedule_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("sched_option:"):
        parts = data.split(":", 2)
        key = parts[1]
        cron = parts[2]

        project_id = context.user_data.get("sched_project_id")
        if not project_id:
            await query.edit_message_text("Error: No project selected.")
            return ConversationHandler.END

        if key == "custom":
            await query.edit_message_text(
                "Enter a custom cron expression:\n\n"
                "Format: `minute hour day month weekday`\n"
                "Example: `0 9 * * 1-5` (weekdays at 9 AM)\n"
                "Example: `*/30 * * * *` (every 30 minutes)\n\n"
                "Tip: Use https://crontab.guru to generate expressions.",
                parse_mode="Markdown",
            )
            return SELECTING_CUSTOM_CRON

        await save_schedule(update, context, project_id, cron)
        return ConversationHandler.END

    if data == "sched_remove":
        project_id = context.user_data.get("sched_project_id")
        if not project_id:
            await query.edit_message_text("Error: No project selected.")
            return ConversationHandler.END

        schedule = db.get_schedule_by_project(project_id)
        if schedule:
            db.delete_schedule(schedule["id"])
            db.set_project_schedule(project_id, None)
            scheduler_manager.remove_job(project_id)

        await query.edit_message_text("✅ Schedule removed.")
        return ConversationHandler.END

    if data == "sched_pause":
        project_id = context.user_data.get("sched_project_id")
        if project_id:
            schedule = db.get_schedule_by_project(project_id)
            if schedule:
                db.enable_schedule(schedule["id"], False)
                scheduler_manager.remove_job(project_id)
            await query.edit_message_text("⏸ Sync paused. Use /schedule to resume.")
        return ConversationHandler.END

    if data == "sched_resume":
        project_id = context.user_data.get("sched_project_id")
        if project_id:
            schedule = db.get_schedule_by_project(project_id)
            if schedule and schedule.get("cron_expression"):
                db.enable_schedule(schedule["id"], True)
                scheduler_manager.add_job(project_id, schedule["cron_expression"])
            await query.edit_message_text("▶ Sync resumed.")
        return ConversationHandler.END

    if data == "sched_back_menu":
        await schedule_start(update, context)
        return SELECTING_PROJECT

    return ConversationHandler.END


async def save_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE, project_id: int, cron: str) -> None:
    ProjectManager.set_schedule(project_id, cron)
    scheduler_manager.add_job(project_id, cron)
    desc = ScheduleService.describe_cron(cron)
    query = update.callback_query
    await query.edit_message_text(
        f"✅ Schedule Set!\n\n{desc}\n\nYour project will auto-sync on this schedule.",
    )


async def receive_custom_cron(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    tokens = raw.split()
    if len(tokens) != 5:
        await update.message.reply_text(
            "❌ Invalid cron expression. Need exactly 5 fields:\n"
            "`minute hour day month weekday`\n\n"
            "Example: `0 9 * * 1-5` (weekdays at 9 AM)\n"
            "Example: `*/30 * * * *` (every 30 minutes)",
            parse_mode="Markdown",
        )
        return SELECTING_CUSTOM_CRON

    cron = " ".join(tokens)
    try:
        CronTrigger.from_crontab(cron)
    except (ValueError, IndexError) as e:
        await update.message.reply_text(
            f"❌ Invalid cron expression: {e}\n\n"
            "Use format: `minute hour day month weekday`\n"
            "Example: `*/15 * * * *`",
            parse_mode="Markdown",
        )
        return SELECTING_CUSTOM_CRON

    project_id = context.user_data.get("sched_project_id")
    if not project_id:
        await update.message.reply_text("Error: No project selected. Use /schedule to start over.")
        return ConversationHandler.END

    ProjectManager.set_schedule(project_id, cron)
    scheduler_manager.add_job(project_id, cron)
    desc = ScheduleService.describe_cron(cron)
    await update.message.reply_text(
        f"✅ Schedule Updated!\n\n{desc}\n\nYour project will auto-sync on this schedule.",
    )

    return ConversationHandler.END


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    user_db = db.get_user_by_telegram_id(user.id)
    if not user_db:
        await update.message.reply_text("Use /start first.")
        return
    projects = db.get_user_projects(user_db["id"])
    if not projects:
        await update.message.reply_text("No projects found.")
        return
    if context.args:
        pid = int(context.args[0])
        projects = [p for p in projects if p["id"] == pid]
    if not projects:
        await update.message.reply_text("No matching project.")
        return
    for p in projects:
        sched = db.get_schedule_by_project(p["id"])
        if sched and sched.get("enabled"):
            db.enable_schedule(sched["id"], False)
            scheduler_manager.remove_job(p["id"])
            await update.message.reply_text(f"⏸ Paused sync for *{p['project_name']}*.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"*{p['project_name']}* has no active schedule.", parse_mode="Markdown")


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    user_db = db.get_user_by_telegram_id(user.id)
    if not user_db:
        await update.message.reply_text("Use /start first.")
        return
    projects = db.get_user_projects(user_db["id"])
    if not projects:
        await update.message.reply_text("No projects found.")
        return
    if context.args:
        pid = int(context.args[0])
        projects = [p for p in projects if p["id"] == pid]
    if not projects:
        await update.message.reply_text("No matching project.")
        return
    for p in projects:
        sched = db.get_schedule_by_project(p["id"])
        if sched and sched.get("cron_expression"):
            if not sched.get("enabled"):
                db.enable_schedule(sched["id"], True)
                scheduler_manager.add_job(p["id"], sched["cron_expression"])
                await update.message.reply_text(f"▶ Resumed sync for *{p['project_name']}*.", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"*{p['project_name']}* is already running.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"*{p['project_name']}* has no schedule set.", parse_mode="Markdown")


async def batchsize_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    user_db = db.get_user_by_telegram_id(user.id)
    if not user_db:
        await update.message.reply_text("Use /start first.")
        return
    projects = db.get_user_projects(user_db["id"])
    if not projects:
        await update.message.reply_text("No projects found.")
        return
    if not context.args:
        lines = ["Current batch sizes:"]
        for p in projects:
            bs = db.get_batch_size(p["id"])
            lines.append(f"  {p['project_name']}: {bs} files")
        lines.append("\nUsage: /batchsize <project_id> <number>")
        lines.append("Example: /batchsize 1 8")
        await update.message.reply_text("\n".join(lines))
        return
    try:
        if len(context.args) == 2:
            pid = int(context.args[0])
            size = int(context.args[1])
            if size < 1 or size > 50:
                await update.message.reply_text("Batch size must be between 1 and 50.")
                return
            project = db.get_project(pid)
            if not project or project["user_id"] != user_db["id"]:
                await update.message.reply_text("Project not found.")
                return
            db.set_batch_size(pid, size)
            await update.message.reply_text(
                f"✅ Batch size for *{project['project_name']}* set to {size} files per sync.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text("Usage: /batchsize <project_id> <number>")
    except ValueError:
        await update.message.reply_text("Usage: /batchsize <project_id> <number>")


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def get_schedule_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("schedule", schedule_start)],
        states={
            SELECTING_PROJECT: [
                CallbackQueryHandler(select_project_callback, pattern="^sched_project:"),
            ],
            SELECTING_SCHEDULE: [
                CallbackQueryHandler(select_schedule_callback, pattern="^(sched_option:|sched_remove|sched_pause|sched_resume|sched_back_menu)"),
            ],
            SELECTING_CUSTOM_CRON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_custom_cron),
                MessageHandler(filters.COMMAND, cancel_conversation),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conversation),
            MessageHandler(filters.COMMAND, cancel_conversation),
        ],
        allow_reentry=True,
        per_user=True,
    )
