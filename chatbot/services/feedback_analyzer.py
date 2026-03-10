from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from timetable.models import TimetableEntry

from .timetable_generator import generate_timetable_for_user


@dataclass
class FeedbackStrategy:
    max_chunk_minutes: int
    priority_boost: int
    extra_minutes_ratio: float
    action: str


def _build_strategy(reason):
    text = (reason or "").lower()

    strategy = FeedbackStrategy(
        max_chunk_minutes=60,
        priority_boost=1,
        extra_minutes_ratio=0.0,
        action="move_topic_to_next_slots",
    )

    if any(token in text for token in ["busy", "no time", "work", "travel", "family"]):
        strategy.max_chunk_minutes = 30
        strategy.action = "split_topic_into_smaller_sessions"

    if any(token in text for token in ["tired", "fatigue", "burnout"]):
        strategy.max_chunk_minutes = min(strategy.max_chunk_minutes, 45)
        strategy.action = "reduce_session_size"

    if any(token in text for token in ["hard", "difficult", "confusing", "weak"]):
        strategy.extra_minutes_ratio = 0.25
        strategy.priority_boost += 1

    return strategy


def _pick_target_entry(user, entry_id=None):
    queryset = TimetableEntry.objects.filter(user=user, done=False)

    if entry_id is not None:
        return queryset.filter(id=entry_id).first()

    overdue = queryset.filter(end__lt=timezone.now()).order_by("-end").first()
    if overdue:
        return overdue

    return queryset.order_by("start").first()


@transaction.atomic
def adaptive_reschedule_for_user(user, reason, entry_id=None):
    target_entry = _pick_target_entry(user=user, entry_id=entry_id)
    if not target_entry:
        return {
            "entries": [],
            "strategy": _build_strategy(reason),
            "message": "No pending timetable entry found to reschedule.",
            "target_entry": None,
        }

    strategy = _build_strategy(reason)
    topic = target_entry.topic

    missed_minutes = max(1, int((target_entry.end - target_entry.start).total_seconds() // 60))
    extra_minutes = int(missed_minutes * strategy.extra_minutes_ratio)

    topic.priority = min(topic.priority + strategy.priority_boost, 10)
    if extra_minutes > 0:
        topic.estimated_minutes += extra_minutes
    topic.save(update_fields=["priority", "estimated_minutes"])

    entries = generate_timetable_for_user(
        user=user,
        max_chunk_minutes=strategy.max_chunk_minutes,
    )

    return {
        "entries": list(entries),
        "strategy": strategy,
        "message": "Adaptive rescheduling completed.",
        "target_entry": target_entry,
        "topic": topic,
        "extra_minutes": extra_minutes,
    }
