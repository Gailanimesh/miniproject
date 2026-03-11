import os

from celery import Celery
from celery.schedules import crontab


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

app = Celery("backend")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "process-notifications-every-minute": {
        "task": "timetable.tasks.process_notifications_and_reschedule",
        "schedule": crontab(minute="*"),
    },
    "daily-auto-reschedule-missed": {
        "task": "timetable.tasks.daily_reschedule_missed_entries",
        "schedule": crontab(hour=0, minute=5),
    },
}
