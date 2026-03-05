from datetime import timedelta
from django.utils import timezone
from django.apps import apps
from .models import Topic, FreeSlot

def schedule_timetable_for_user(user):
    TimetableEntry = apps.get_model('timetable', 'TimetableEntry')

    """
    Simple greedy scheduler:
    - get unfinished topics sorted by priority desc and remaining minutes
    - fill free slots in chronological order
    - create TimetableEntry chunks of up to slot length or remaining topic time
    """
    topics = list(Topic.objects.filter(user=user).order_by('-priority', 'id'))
    for t in topics:
        t.remaining = max(0, t.estimated_minutes - t.completed_minutes)

    free_slots = list(FreeSlot.objects.filter(user=user).order_by('start'))
    entries = []
    for slot in free_slots:
        slot_start = slot.start
        slot_end = slot.end
        available = int((slot_end - slot_start).total_seconds() // 60)
        if available <= 0:
            continue
        while available > 0 and any(t.remaining > 0 for t in topics):
            topic = next((t for t in topics if t.remaining > 0), None)
            if not topic:
                break
            take = min(available, topic.remaining, 60)
            chunk_start = slot_start
            chunk_end = chunk_start + timedelta(minutes=take)
            entries.append(TimetableEntry(user=user, topic_id=topic.id, start=chunk_start, end=chunk_end))
            slot_start = chunk_end
            available -= take
            topic.remaining -= take

    TimetableEntry.objects.filter(user=user, start__gte=timezone.now()).delete()
    if entries:
        TimetableEntry.objects.bulk_create(entries)
    return TimetableEntry.objects.filter(user=user).order_by('start')