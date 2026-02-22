from celery import Celery
from ..platform.config import settings

celery_app = Celery(
    "taali",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "workable-sync-every-30-minutes": {
            "task": "app.tasks.assessment_tasks.sync_workable_orgs",
            "schedule": 1800.0,
        },
        "assessment-expiry-reminders-daily": {
            "task": "app.tasks.assessment_tasks.send_assessment_expiry_reminders",
            "schedule": 86400.0,
        },
    },
)

# Auto-discover tasks
celery_app.autodiscover_tasks(["app.tasks"])
