import logging
from typing import Optional, Callable
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from database import db
from config import config

logger = logging.getLogger(__name__)

JOB_STORE_URL = f"sqlite:///{config.DATABASE_PATH}"

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

    def add_job(self, project_id: int, cron_expression: str) -> str:
        job_id = f"sync_project_{project_id}"
        trigger = CronTrigger.from_crontab(cron_expression)

        self.scheduler.add_job(
            _job_executor,
            trigger=trigger,
            id=job_id,
            name=f"Sync Project {project_id}",
            replace_existing=True,
            kwargs={"project_id": project_id},
        )
        logger.info(f"Added schedule job {job_id} with cron: {cron_expression}")
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
                return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S IST")
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
            cron_expr = sched["cron_expression"]
            if cron_expr:
                self.add_job(project_id, cron_expr)
        logger.info(f"Rescheduled {len(schedules)} jobs from database.")


scheduler_manager = SchedulerManager()
