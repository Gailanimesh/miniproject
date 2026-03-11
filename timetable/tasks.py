try:
    from celery import shared_task
except Exception:  # pragma: no cover - fallback when Celery is not installed
    def shared_task(*decorator_args, **decorator_kwargs):
        if (
            decorator_args
            and len(decorator_args) == 1
            and callable(decorator_args[0])
            and not decorator_kwargs
        ):
            return decorator_args[0]

        def _decorator(func):
            return func

        return _decorator

from .notification_service import daily_reschedule_missed_entries, process_notification_pipeline


@shared_task(name="timetable.tasks.process_notifications_and_reschedule")
def process_notifications_and_reschedule():
    return process_notification_pipeline()


@shared_task(name="timetable.tasks.daily_reschedule_missed_entries")
def daily_reschedule_missed_entries_task():
    return {"rescheduled_entries": daily_reschedule_missed_entries()}
