import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from database import db
from config import config

IST = timezone(timedelta(hours=5, minutes=30))

def format_ist(dt: datetime) -> str:
    """Format a datetime as a human-friendly IST time string."""
    t = dt.astimezone(IST)
    return t.strftime("%I:%M %p IST").lstrip("0").replace("  ", " ")

def now_ist() -> str:
    """Return the current IST time as a human-friendly string."""
    return format_ist(datetime.now(IST))

logger = logging.getLogger(__name__)

import os
JOB_STORE_URL = os.environ.get("DATABASE_URL") or "sqlite:///database/apscheduler.db"
if JOB_STORE_URL.startswith("postgres://"):
    JOB_STORE_URL = "postgresql" + JOB_STORE_URL[len("postgres"):]

# Module-level callback registry so APScheduler doesn't try to pickle a
# scheduler instance when serializing job references.
_sync_callback_global: Optional[Callable] = None


async def _job_executor(project_id: int) -> None:
    """Module-level async function used as APScheduler job target.
    AsyncIOScheduler natively supports async targets, so this runs
    inside the application's event loop automatically.
    """
    cb = _sync_callback_global
    if cb is None:
        logger.warning("No sync callback registered for job.")
        return
    try:
        await cb(project_id)
    except Exception as e:
        logger.exception(f"Scheduled sync failed for project {project_id}: {e}")


class SchedulerManager:
    _instance: Optional["SchedulerManager"] = None

    def __new__(cls) -> "SchedulerManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True
        self.scheduler: AsyncIOScheduler = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=JOB_STORE_URL)},
            timezone="Asia/Kolkata",
        )

    def set_sync_callback(self, callback: Callable) -> None:
        global _sync_callback_global
        _sync_callback_global = callback

    def start(self) -> None:
        self.scheduler.start()
        logger.info("Scheduler started.")

    def stop(self) -> None:
        self.scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")

    def add_job(self, project_id: int, expression: str) -> str:
        job_id = f"sync_project_{project_id}"

        if expression.startswith("interval_minutes:"):
            minutes = int(expression.split(":")[1])
            trigger = IntervalTrigger(minutes=minutes)
        elif expression.startswith("interval:"):
            hours = int(expression.split(":")[1])
            trigger = IntervalTrigger(hours=hours)
        else:
            trigger = CronTrigger.from_crontab(expression)

        self.scheduler.add_job(
            _job_executor,
            trigger=trigger,
            id=job_id,
            name=f"Sync Project {project_id}",
            replace_existing=True,
            kwargs={"project_id": project_id},
        )
        logger.info(f"Added schedule job {job_id}: {expression}")
        return job_id

    def remove_job(self, project_id: int) -> None:
        job_id = f"sync_project_{project_id}"
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed schedule job {job_id}")
        except Exception:
            pass

    def get_next_run_time(self, project_id: int) -> Optional[str]:
        job_id = f"sync_project_{project_id}"
        try:
            job = self.scheduler.get_job(job_id)
            if job and job.next_run_time:
                return format_ist(job.next_run_time)
        except Exception:
            pass
        return None

    def job_exists(self, project_id: int) -> bool:
        job_id = f"sync_project_{project_id}"
        try:
            return self.scheduler.get_job(job_id) is not None
        except Exception:
            return False

    def reschedule_all(self) -> None:
        schedules = db.get_all_enabled_schedules()
        for sched in schedules:
            project_id = sched["project_id"]
            expr = sched["cron_expression"]
            if expr:
                self.add_job(project_id, expr)
        logger.info(f"Rescheduled {len(schedules)} jobs from database.")


scheduler_manager = SchedulerManager()
