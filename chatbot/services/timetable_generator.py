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


def generate_timetable(
    user_profile,
    topics,
    free_slots,
    exam_subjects,
    max_chunk_minutes=60,
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
            active_topic = next((row for row in topic_state if row["remaining"] > 0), None)
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


def generate_timetable_for_user(user, max_chunk_minutes=60):
    topics = Topic.objects.filter(user=user)
    free_slots = FreeSlot.objects.filter(user=user)
    exam_subjects = ExamSubject.objects.filter(user=user)
    user_profile = UserProfile.objects.filter(user=user).first()

    generated_entries = generate_timetable(
        user_profile=user_profile,
        topics=topics,
        free_slots=free_slots,
        exam_subjects=exam_subjects,
        max_chunk_minutes=max_chunk_minutes,
    )

    if not generated_entries:
        return schedule_timetable_for_user(user, max_chunk_minutes=max_chunk_minutes)

    TimetableEntry.objects.filter(user=user, start__gte=timezone.now()).delete()
    TimetableEntry.objects.bulk_create(generated_entries)
    return TimetableEntry.objects.filter(user=user).order_by("start")
