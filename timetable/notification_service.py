from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from chatbot.services.feedback_analyzer import adaptive_reschedule_for_user
from chatbot.services.timetable_generator import generate_timetable_for_user

from .models import CompletionCheck, TimetableEntry, UserNotification


PRE_REMINDER_MINUTES = 10
COMPLETION_RESPONSE_GRACE_MINUTES = 30
COMPLETION_LOOKBACK_HOURS = 12


def _entry_duration_minutes(entry):
    return max(1, int((entry.end - entry.start).total_seconds() // 60))


def _entry_payload(entry):
    return {
        "entry_id": entry.id,
        "topic": entry.topic.name,
        "topic_id": entry.topic_id,
        "start": entry.start.isoformat(),
        "end": entry.end.isoformat(),
        "duration_minutes": _entry_duration_minutes(entry),
    }


def build_quiz_question(topic_name):
    return (
        f"Quick check for {topic_name}: name one key concept and where you can apply it."
    )


def create_pre_reminder_notifications(now=None, lead_minutes=PRE_REMINDER_MINUTES):
    now = now or timezone.now()
    horizon = now + timedelta(minutes=lead_minutes)

    upcoming = list(
        TimetableEntry.objects.filter(
            done=False,
            notified=False,
            start__gt=now,
            start__lte=horizon,
        )
        .select_related("topic", "user")
        .order_by("start")
    )

    created = 0
    for entry in upcoming:
        exists = UserNotification.objects.filter(
            entry=entry,
            notification_type=UserNotification.TYPE_PRE_REMINDER,
            is_actioned=False,
        ).exists()
        if exists:
            continue

        UserNotification.objects.create(
            user=entry.user,
            entry=entry,
            notification_type=UserNotification.TYPE_PRE_REMINDER,
            title="Study session in 10 minutes",
            message=(
                f"Your topic '{entry.topic.name}' starts at "
                f"{entry.start.strftime('%H:%M')}"
            ),
            payload=_entry_payload(entry),
            scheduled_for=entry.start - timedelta(minutes=lead_minutes),
            sent_at=now,
        )
        entry.notified = True
        entry.save(update_fields=["notified"])
        created += 1

    return created


def create_completion_check_notifications(
    now=None,
    response_grace_minutes=COMPLETION_RESPONSE_GRACE_MINUTES,
):
    now = now or timezone.now()
    lookback = now - timedelta(hours=COMPLETION_LOOKBACK_HOURS)

    completed_recently = list(
        TimetableEntry.objects.filter(
            done=False,
            end__lte=now,
            end__gte=lookback,
            completion_check__isnull=True,
        )
        .select_related("topic", "user")
        .order_by("end")
    )

    created = 0
    for entry in completed_recently:
        prompt = build_quiz_question(entry.topic.name)
        check = CompletionCheck.objects.create(
            entry=entry,
            user=entry.user,
            asked_at=now,
            quiz_question=prompt,
        )

        UserNotification.objects.create(
            user=entry.user,
            entry=entry,
            notification_type=UserNotification.TYPE_COMPLETION_CHECK,
            title="Did you complete this study session?",
            message=(
                f"Topic '{entry.topic.name}' ended. Mark complete or ask for reschedule."
            ),
            payload={
                **_entry_payload(entry),
                "completion_check_id": check.id,
                "quiz_question": prompt,
            },
            scheduled_for=entry.end,
            sent_at=now,
            action_due_at=now + timedelta(minutes=response_grace_minutes),
        )
        created += 1

    return created


def create_reschedule_notification(user, entry, result, reason, now=None):
    now = now or timezone.now()
    strategy = result.get("strategy")
    action = getattr(strategy, "action", "reschedule")

    payload = {
        "entry_id": entry.id if entry else None,
        "reason": reason,
        "strategy_action": action,
        "extra_minutes": result.get("extra_minutes", 0),
        "generated_entries": len(result.get("entries", [])),
        "generation": result.get("generation_meta", {}),
    }
    if entry is not None:
        payload["topic"] = entry.topic.name

    UserNotification.objects.create(
        user=user,
        entry=entry,
        notification_type=UserNotification.TYPE_RESCHEDULE,
        title="Timetable was auto-rescheduled",
        message="Your upcoming study plan was updated based on missed progress.",
        payload=payload,
        scheduled_for=now,
        sent_at=now,
        is_actioned=True,
    )


def auto_reschedule_pending_checks(
    now=None,
    overdue_minutes=COMPLETION_RESPONSE_GRACE_MINUTES,
):
    now = now or timezone.now()
    cutoff = now - timedelta(minutes=overdue_minutes)

    pending_checks = list(
        CompletionCheck.objects.filter(
            completed__isnull=True,
            auto_rescheduled=False,
            asked_at__lte=cutoff,
            entry__done=False,
        ).select_related("entry", "entry__topic", "user")
    )

    processed = 0
    for check in pending_checks:
        reason = (
            check.response_text.strip()
            if check.response_text
            else "No completion confirmation after session."
        )
        result = adaptive_reschedule_for_user(
            user=check.user,
            reason=reason,
            entry_id=check.entry_id,
        )

        check.auto_rescheduled = True
        check.completed = False
        check.response_received_at = now
        check.save(update_fields=["auto_rescheduled", "completed", "response_received_at"])

        create_reschedule_notification(
            user=check.user,
            entry=check.entry,
            result=result,
            reason=reason,
            now=now,
        )
        UserNotification.objects.filter(
            entry=check.entry,
            notification_type=UserNotification.TYPE_COMPLETION_CHECK,
            is_actioned=False,
        ).update(is_actioned=True)

        processed += 1

    return processed


@transaction.atomic
def apply_completion_response(
    *,
    user,
    entry,
    completed,
    response_text="",
    quiz_answer="",
):
    now = timezone.now()
    check, _ = CompletionCheck.objects.get_or_create(
        entry=entry,
        defaults={
            "user": user,
            "asked_at": now,
            "quiz_question": build_quiz_question(entry.topic.name),
        },
    )

    check.user = user
    check.completed = bool(completed)
    check.response_text = response_text or ""
    check.quiz_answer = quiz_answer or ""
    check.response_received_at = now
    check.save(
        update_fields=[
            "user",
            "completed",
            "response_text",
            "quiz_answer",
            "response_received_at",
        ]
    )

    UserNotification.objects.filter(
        entry=entry,
        notification_type=UserNotification.TYPE_COMPLETION_CHECK,
    ).update(is_actioned=True, is_read=True)

    if completed:
        if not entry.done:
            entry.done = True
            entry.save(update_fields=["done"])
            duration = _entry_duration_minutes(entry)
            topic = entry.topic
            topic.completed_minutes = (topic.completed_minutes or 0) + duration
            topic.save(update_fields=["completed_minutes"])

        entries, generation_meta = generate_timetable_for_user(
            user=user,
            include_metadata=True,
            use_model_priority=True,
        )
        return {
            "status": "completed",
            "entry_done": True,
            "entries": list(entries),
            "generation": generation_meta,
            "completion_check": check,
        }

    reason = response_text or "User marked session as not completed."
    result = adaptive_reschedule_for_user(user=user, reason=reason, entry_id=entry.id)

    check.auto_rescheduled = True
    check.save(update_fields=["auto_rescheduled"])
    create_reschedule_notification(user=user, entry=entry, result=result, reason=reason, now=now)

    return {
        "status": "rescheduled",
        "entry_done": False,
        "reschedule": result,
        "completion_check": check,
    }


def process_notification_pipeline(now=None):
    now = now or timezone.now()
    pre_count = create_pre_reminder_notifications(now=now)
    completion_count = create_completion_check_notifications(now=now)
    rescheduled_count = auto_reschedule_pending_checks(now=now)

    return {
        "pre_reminders": pre_count,
        "completion_checks": completion_count,
        "auto_rescheduled": rescheduled_count,
    }


def daily_reschedule_missed_entries(now=None):
    now = now or timezone.now()
    cutoff = now - timedelta(hours=24)
    stale_entries = list(
        TimetableEntry.objects.filter(done=False, end__lte=cutoff).select_related("user", "topic")
    )

    processed_entry_ids = set()
    count = 0

    for entry in stale_entries:
        if entry.id in processed_entry_ids:
            continue

        check, _ = CompletionCheck.objects.get_or_create(
            entry=entry,
            defaults={
                "user": entry.user,
                "asked_at": now,
                "quiz_question": build_quiz_question(entry.topic.name),
            },
        )

        if check.auto_rescheduled:
            continue

        result = adaptive_reschedule_for_user(
            user=entry.user,
            reason="Daily automatic reschedule for uncompleted sessions.",
            entry_id=entry.id,
        )
        check.auto_rescheduled = True
        check.completed = False
        check.response_received_at = now
        check.save(update_fields=["auto_rescheduled", "completed", "response_received_at"])

        create_reschedule_notification(
            user=entry.user,
            entry=entry,
            result=result,
            reason="Daily automatic reschedule for uncompleted sessions.",
            now=now,
        )
        processed_entry_ids.add(entry.id)
        count += 1

    return count
