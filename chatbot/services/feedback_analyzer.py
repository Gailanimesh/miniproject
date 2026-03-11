from dataclasses import dataclass
import re

from django.db import transaction
from django.utils import timezone

from timetable.models import TimetableEntry

from .timetable_generator import generate_timetable_for_user


SIGNAL_KEYWORDS = {
    "time_constraints": [
        "busy",
        "no time",
        "work",
        "travel",
        "family",
        "meeting",
        "deadline",
        "shift",
    ],
    "fatigue": [
        "tired",
        "fatigue",
        "burnout",
        "exhausted",
        "drained",
        "sleepy",
    ],
    "difficulty": [
        "hard",
        "difficult",
        "confusing",
        "weak",
        "stuck",
        "tough",
        "complex",
    ],
    "urgency": [
        "urgent",
        "asap",
        "soon",
        "tomorrow",
        "next day",
        "exam near",
    ],
}


@dataclass
class FeedbackAnalysis:
    reason: str
    matched_keywords: dict
    confidence: str

    def signal_flags(self):
        return {signal: bool(tokens) for signal, tokens in self.matched_keywords.items()}

    def to_dict(self):
        return {
            "reason": self.reason,
            "signals": self.signal_flags(),
            "matched_keywords": self.matched_keywords,
            "confidence": self.confidence,
        }


@dataclass
class FeedbackStrategy:
    max_chunk_minutes: int
    priority_boost: int
    extra_minutes_ratio: float
    action: str

    def to_dict(self):
        return {
            "max_chunk_minutes": self.max_chunk_minutes,
            "priority_boost": self.priority_boost,
            "extra_minutes_ratio": self.extra_minutes_ratio,
            "action": self.action,
        }


def _token_in_text(text, token):
    if " " in token:
        return token in text
    pattern = r"\b" + re.escape(token) + r"\b"
    return bool(re.search(pattern, text))


def _match_keywords(reason_text):
    text = f" {(reason_text or '').lower()} "
    matched = {}

    for signal, keywords in SIGNAL_KEYWORDS.items():
        hits = [keyword for keyword in keywords if _token_in_text(text, keyword)]
        matched[signal] = sorted(set(hits))

    return matched


def _confidence_from_matches(matched_keywords):
    hit_count = sum(len(items) for items in matched_keywords.values())
    if hit_count >= 3:
        return "high"
    if hit_count >= 1:
        return "medium"
    return "low"


def _analyze_reason(reason):
    reason = (reason or "").strip()
    matched_keywords = _match_keywords(reason)
    confidence = _confidence_from_matches(matched_keywords)
    return FeedbackAnalysis(
        reason=reason,
        matched_keywords=matched_keywords,
        confidence=confidence,
    )


def _build_strategy(analysis):
    flags = analysis.signal_flags()

    strategy = FeedbackStrategy(
        max_chunk_minutes=60,
        priority_boost=1,
        extra_minutes_ratio=0.0,
        action="move_topic_to_next_slots",
    )

    if flags.get("time_constraints"):
        strategy.max_chunk_minutes = 30
        strategy.action = "split_topic_into_smaller_sessions"

    if flags.get("fatigue"):
        strategy.max_chunk_minutes = min(strategy.max_chunk_minutes, 45)
        if strategy.action == "move_topic_to_next_slots":
            strategy.action = "reduce_session_size"
        else:
            strategy.action = "split_topic_and_reduce_load"

    if flags.get("difficulty"):
        strategy.max_chunk_minutes = min(strategy.max_chunk_minutes, 45)
        strategy.priority_boost += 1
        strategy.extra_minutes_ratio = max(strategy.extra_minutes_ratio, 0.25)
        if strategy.action == "move_topic_to_next_slots":
            strategy.action = "increase_focus_for_difficult_topic"

    if flags.get("urgency"):
        strategy.priority_boost += 1
        strategy.extra_minutes_ratio = max(strategy.extra_minutes_ratio, 0.1)
        if flags.get("time_constraints"):
            strategy.action = "prioritize_and_split_topic"
        else:
            strategy.action = "prioritize_urgent_topic"

    return strategy


def _pick_target_entry(user, entry_id=None):
    queryset = TimetableEntry.objects.filter(user=user, done=False)

    if entry_id is not None:
        return queryset.filter(id=entry_id).first()

    now = timezone.now()
    overdue = queryset.filter(end__lt=now).order_by("-end").first()
    if overdue:
        return overdue

    upcoming = queryset.filter(start__gte=now).order_by("start").first()
    if upcoming:
        return upcoming

    return queryset.order_by("start").first()


@transaction.atomic
def adaptive_reschedule_for_user(user, reason, entry_id=None):
    analysis = _analyze_reason(reason)
    strategy = _build_strategy(analysis)
    target_entry = _pick_target_entry(user=user, entry_id=entry_id)

    if not target_entry:
        return {
            "entries": [],
            "strategy": strategy,
            "analysis": analysis,
            "message": "No pending timetable entry found to reschedule.",
            "target_entry": None,
            "topic_before": None,
            "topic_after": None,
            "extra_minutes": 0,
            "generation_meta": {
                "algorithm": "none",
                "ai_used": False,
            },
        }

    topic = target_entry.topic
    topic_before = {
        "priority": topic.priority,
        "estimated_minutes": topic.estimated_minutes,
    }

    missed_minutes = max(
        1,
        int((target_entry.end - target_entry.start).total_seconds() // 60),
    )
    extra_minutes = int(round(missed_minutes * strategy.extra_minutes_ratio))

    topic.priority = min(topic.priority + strategy.priority_boost, 10)
    if extra_minutes > 0:
        topic.estimated_minutes += extra_minutes

    update_fields = ["priority"]
    if extra_minutes > 0:
        update_fields.append("estimated_minutes")
    topic.save(update_fields=update_fields)

    entries, generation_meta = generate_timetable_for_user(
        user=user,
        max_chunk_minutes=strategy.max_chunk_minutes,
        include_metadata=True,
        use_model_priority=True,
    )

    topic_after = {
        "priority": topic.priority,
        "estimated_minutes": topic.estimated_minutes,
    }

    return {
        "entries": list(entries),
        "strategy": strategy,
        "analysis": analysis,
        "message": "Adaptive rescheduling completed.",
        "target_entry": target_entry,
        "topic": topic,
        "topic_before": topic_before,
        "topic_after": topic_after,
        "extra_minutes": extra_minutes,
        "generation_meta": generation_meta,
    }
