from datetime import timedelta

from django.utils import timezone

from timetable.models import ExamSubject, FreeSlot, TimetableEntry, Topic
from timetable.services import schedule_timetable_for_user
from users.models import UserProfile

KNOWLEDGE_FACTORS = {
    "beginner": 1.25,
    "intermediate": 1.1,
    "advanced": 0.9,
}

DIFFICULTY_FACTORS = {
    "easy": 0.85,
    "medium": 1.0,
    "hard": 1.2,
}


def _exam_proximity_bonus(topic, exam_subjects, today):
    topic_name = topic.name.lower()
    best_bonus = 0.0

    for subject in exam_subjects:
        subject_name = subject.name.lower()
        if subject_name not in topic_name and topic_name not in subject_name:
            continue

        days_left = max((subject.exam_date - today).days, 0)
        if days_left <= 7:
            proximity_factor = 2.0
        elif days_left <= 14:
            proximity_factor = 1.5
        elif days_left <= 30:
            proximity_factor = 1.0
        else:
            proximity_factor = 0.5

        difficulty_factor = DIFFICULTY_FACTORS.get(subject.difficulty.lower(), 1.0)
        best_bonus = max(best_bonus, proximity_factor * difficulty_factor)

    return best_bonus


def _build_topic_state(user_profile, topics, exam_subjects):
    knowledge_level = (getattr(user_profile, "knowledge_level", "") or "").lower()
    knowledge_factor = KNOWLEDGE_FACTORS.get(knowledge_level, 1.0)
    today = timezone.now().date()

    state = []
    for topic in topics:
        remaining = max(0, topic.estimated_minutes - topic.completed_minutes)
        if remaining <= 0:
            continue

        exam_bonus = _exam_proximity_bonus(topic, exam_subjects, today)
        score = (topic.priority * 2.0 + exam_bonus) * knowledge_factor
        state.append(
            {
                "topic": topic,
                "remaining": remaining,
                "score": score,
            }
        )

    state.sort(key=lambda row: (-row["score"], row["topic"].id))
    return state


def _pick_topic_for_slot(
    topic_state,
    slot_start,
    available,
    max_chunk_minutes,
    ml_ranker=None,
):
    candidates = [row for row in topic_state if row["remaining"] > 0]
    if not candidates:
        return None

    if ml_ranker is None:
        return candidates[0]

    best_row = None
    best_key = None

    for row in candidates:
        take = min(available, row["remaining"], max_chunk_minutes)
        if take <= 0:
            continue

        predicted_completion = 0.5
        try:
            predicted_completion = float(
                ml_ranker.score_topic_slot(
                    topic=row["topic"],
                    start=slot_start,
                    end=slot_start + timedelta(minutes=take),
                )
            )
        except Exception:
            predicted_completion = 0.5

        weighted_score = row["score"] * (0.7 + 0.6 * predicted_completion)
        row["ml_completion_prob"] = predicted_completion
        key = (weighted_score, predicted_completion, -row["topic"].id)
        if best_key is None or key > best_key:
            best_row = row
            best_key = key

    return best_row or candidates[0]


def generate_timetable(
    user_profile,
    topics,
    free_slots,
    exam_subjects,
    max_chunk_minutes=60,
    ml_ranker=None,
):
    topics = list(topics)
    free_slots = sorted(list(free_slots), key=lambda slot: slot.start)
    exam_subjects = list(exam_subjects)

    if not topics or not free_slots:
        return []

    topic_state = _build_topic_state(user_profile, topics, exam_subjects)
    if not topic_state:
        return []

    entries = []
    for slot in free_slots:
        slot_start = slot.start
        slot_end = slot.end
        available = int((slot_end - slot_start).total_seconds() // 60)

        while available > 0:
            active_topic = _pick_topic_for_slot(
                topic_state=topic_state,
                slot_start=slot_start,
                available=available,
                max_chunk_minutes=max_chunk_minutes,
                ml_ranker=ml_ranker,
            )
            if not active_topic:
                break

            take = min(available, active_topic["remaining"], max_chunk_minutes)
            chunk_end = slot_start + timedelta(minutes=take)
            entries.append(
                TimetableEntry(
                    user=active_topic["topic"].user,
                    topic=active_topic["topic"],
                    start=slot_start,
                    end=chunk_end,
                )
            )
            slot_start = chunk_end
            available -= take
            active_topic["remaining"] -= take

            topic_state.sort(
                key=lambda row: (
                    -row["score"],
                    row["remaining"] == 0,
                    row["topic"].id,
                )
            )

    return entries


def generate_timetable_for_user(
    user,
    max_chunk_minutes=60,
    include_metadata=False,
    ml_ranker=None,
    use_model_priority=False,
):
    topics = Topic.objects.filter(user=user)
    free_slots = FreeSlot.objects.filter(user=user)
    exam_subjects = ExamSubject.objects.filter(user=user)
    user_profile = UserProfile.objects.filter(user=user).first()

    ml_training_meta = None
    ml_requested = bool(ml_ranker) or bool(use_model_priority)

    if use_model_priority and ml_ranker is None:
        from .ml_completion_model import build_user_completion_ranker

        ml_ranker, ml_training_meta = build_user_completion_ranker(user)

    if ml_ranker is not None and ml_training_meta is None:
        metadata_fn = getattr(ml_ranker, "metadata", None)
        if callable(metadata_fn):
            try:
                ml_training_meta = metadata_fn()
            except Exception:
                ml_training_meta = {"trained": False, "reason": "metadata_error"}

    generated_entries = generate_timetable(
        user_profile=user_profile,
        topics=topics,
        free_slots=free_slots,
        exam_subjects=exam_subjects,
        max_chunk_minutes=max_chunk_minutes,
        ml_ranker=ml_ranker,
    )

    if not generated_entries:
        fallback_entries = schedule_timetable_for_user(
            user,
            max_chunk_minutes=max_chunk_minutes,
        )
        if include_metadata:
            metadata = {
                "algorithm": "greedy_fallback",
                "ai_used": False,
                "reason": "no_exam_weighted_entries_generated",
                "ml_ranker_requested": ml_requested,
                "ml_ranker_used": bool(ml_ranker),
                "planning_order": [
                    "ml_ranker",
                    "score_weighted_exam_aware",
                    "greedy_fallback",
                ],
                "planner_stage_used": "greedy_fallback",
            }
            if ml_training_meta is not None:
                metadata["ml_training"] = ml_training_meta
            return fallback_entries, metadata
        return fallback_entries

    TimetableEntry.objects.filter(user=user, start__gte=timezone.now()).delete()
    TimetableEntry.objects.bulk_create(generated_entries)
    saved_entries = TimetableEntry.objects.filter(user=user).order_by("start")
    if include_metadata:
        metadata = {
            "algorithm": "score_weighted_exam_aware",
            "ai_used": True,
            "ml_ranker_requested": ml_requested,
            "ml_ranker_used": bool(ml_ranker),
            "planning_order": [
                "ml_ranker",
                "score_weighted_exam_aware",
                "greedy_fallback",
            ],
            "planner_stage_used": "ml_plus_ai" if ml_ranker else "ai_without_ml",
        }
        if ml_training_meta is not None:
            metadata["ml_training"] = ml_training_meta
        return saved_entries, metadata
    return saved_entries
