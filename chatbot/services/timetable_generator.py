import re
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


# ---------------------------------------------------------------------------
# Phase 1 helpers: Data Integrity
# ---------------------------------------------------------------------------

def normalize_topic_name(name):
    """Lowercase, strip special chars, and basic singularize to prevent duplicates."""
    clean = re.sub(r"[^a-z0-9\s]", "", str(name).lower()).strip()
    # Remove trailing 's' for plural merging (e.g. "datastructures" → "datastructure")
    if clean.endswith("s") and len(clean) > 4 and not clean.endswith("ss"):
        clean = clean[:-1]
    return clean


def determine_time_horizon(user_message=""):
    """Classify the planning intent from the user's message."""
    msg = (user_message or "").lower()
    short_term_signals = ["exam", "test", "deadline", "next week", "monday", "tuesday",
                          "wednesday", "thursday", "friday", "by", "before"]
    long_term_signals = ["month", "semester", "year", "3 month", "6 month", "long term"]
    if any(w in msg for w in short_term_signals):
        return "SHORT_TERM"
    if any(w in msg for w in long_term_signals):
        return "LONG_TERM"
    return "CONTINUOUS"


def _filter_and_update_topics(topics):
    """
    Filter topics based on their target_date.
    - Only include topics with future or no target_date
    - Mark overdue topics (target_date passed but work remains)
    Returns: (filtered_topics, overdue_topics)
    """
    today = timezone.now().date()
    filtered = []
    overdue = []
    
    for topic in topics:
        remaining = max(0, topic.estimated_minutes - topic.completed_minutes)
        
        if topic.target_date:
            if topic.target_date < today and remaining > 0:
                topic.is_overdue = True
                topic.save(update_fields=['is_overdue'])
                overdue.append(topic)
            elif topic.target_date >= today:
                filtered.append(topic)
        else:
            filtered.append(topic)
    
    return filtered, overdue


def _get_topic_deadline(topic, user_profile, exam_subjects):
    """
    Get the deadline for a specific topic.
    Priority: topic.target_date > matching exam_subject date > profile.exam_date > None
    """
    if topic.target_date:
        return topic.target_date
    
    topic_name = normalize_topic_name(topic.name)
    
    for subject in exam_subjects:
        subject_name = normalize_topic_name(subject.name)
        if topic_name in subject_name or subject_name in topic_name:
            return subject.exam_date
    
    if user_profile and getattr(user_profile, "exam_date", None):
        return user_profile.exam_date
    
    return None


def _get_latest_target_date(topics, exam_subjects, user_profile):
    """
    Get the latest target date from:
    1. Topic target_dates (if any)
    2. Exam subject dates
    3. Profile exam_date
    Returns the latest date or None if no dates exist.
    """
    from datetime import date
    
    latest = None
    
    for topic in topics:
        if topic.target_date:
            if latest is None or topic.target_date > latest:
                latest = topic.target_date
    
    for subject in exam_subjects:
        if latest is None or subject.exam_date > latest:
            latest = subject.exam_date
    
    if user_profile and getattr(user_profile, "exam_date", None):
        profile_date = user_profile.exam_date
        if isinstance(profile_date, date):
            if latest is None or profile_date > latest:
                latest = profile_date
    
    return latest


# ---------------------------------------------------------------------------
# Phase 2: Smart Slot Extrapolation
# ---------------------------------------------------------------------------

def _extrapolate_free_slots(user, existing_slots, topics, exam_subjects, user_profile,
                             planning_type="SHORT_TERM", max_days=45):
    slots = list(existing_slots)
    if not slots:
        return []

    unique_dates = {s.start.date() for s in slots}

    # Don't extrapolate if the user has already defined a multi-week schedule
    if len(unique_dates) > 5 or (max(unique_dates) - min(unique_dates)).days > 6:
        return slots

    today = timezone.now().date()

    # Determine target end date based on planning horizon
    if planning_type == "SHORT_TERM":
        max_exam_date = _get_latest_target_date(topics, exam_subjects, user_profile)
        
        if not max_exam_date:
            max_exam_date = today + timedelta(days=30)
        
        # Ensure we have enough days to cover all topic deadlines
        # Calculate total study time needed for each topic with a deadline
        filtered_topics, _ = _filter_and_update_topics(topics)
        
        # Add extra buffer days beyond the latest deadline
        # This ensures topics with different deadlines get their share of time
        latest_deadline = None
        for topic in filtered_topics:
            topic_deadline = _get_topic_deadline(topic, user_profile, exam_subjects)
            if topic_deadline:
                if latest_deadline is None or topic_deadline > latest_deadline:
                    latest_deadline = topic_deadline
        
        # Use the later of exam date or latest topic deadline
        if latest_deadline and latest_deadline > max_exam_date:
            max_exam_date = latest_deadline
            
        # Hard cap at max_days to prevent runaway generation
        target_end_date = min(max_exam_date, today + timedelta(days=max_days))
    elif planning_type == "LONG_TERM":
        target_end_date = today + timedelta(days=90)
    else:  # CONTINUOUS
        target_end_date = today + timedelta(days=14)

    first_day = min(unique_dates)
    base_slots = [s for s in slots if s.start.date() == first_day]

    # Track signatures to avoid duplicate virtual slots
    seen_sigs = {(s.start, s.end) for s in slots}

    current_date = first_day + timedelta(days=1)
    while current_date < target_end_date:
        # Dynamic Skip Days (including safety crunch for short term exams)
        t_val = target_end_date.date() if hasattr(target_end_date, "date") else target_end_date
        c_val = current_date.date() if hasattr(current_date, "date") else current_date
        days_to_exam = t_val.toordinal() - c_val.toordinal()
        current_day_name = c_val.strftime("%A")
        
        profile_skips = [s.capitalize() for s in getattr(user_profile, "skip_days", []) or []]
        # Always default to Sunday skip if nothing is specified, for healthy pacing
        if not profile_skips:
            profile_skips = ["Sunday"]

        is_skip_day = current_day_name in profile_skips
        # SAFETY: Last 2 days before an exam are NEVER skipped in short-term mode
        if is_skip_day and not (planning_type == "SHORT_TERM" and days_to_exam <= 2):
            current_date += timedelta(days=1)
            continue

        if current_date not in unique_dates:
            d_diff = current_date.toordinal() - first_day.toordinal()
            diff = timedelta(days=d_diff)
            for bs in base_slots:
                v_start = bs.start + diff
                v_end = bs.end + diff
                sig = (v_start, v_end)
                if sig not in seen_sigs:
                    seen_sigs.add(sig)
                    slots.append(FreeSlot(user=user, start=v_start, end=v_end))

        current_date += timedelta(days=1)

    return sorted(slots, key=lambda s: s.start)


# ---------------------------------------------------------------------------
# Score helpers
# ---------------------------------------------------------------------------

def _deadline_proximity_bonus(topic, today):
    """
    Calculate bonus based on the topic's own target_date.
    Higher bonus for topics with imminent deadlines.
    """
    if not topic.target_date:
        return 0.0
    
    days_left = max((topic.target_date - today).days, 0)
    
    # Same urgency scale as exam proximity
    if days_left == 0:
        return 5.0  # Due today - maximum urgency
    elif days_left <= 2:
        return 4.0  # Due very soon
    elif days_left <= 7:
        return 3.0  # Due this week
    elif days_left <= 14:
        return 2.0  # Due in 2 weeks
    elif days_left <= 30:
        return 1.0  # Due in a month
    else:
        return 0.5  # Far future


def _exam_proximity_bonus(topic, exam_subjects, today):
    topic_name = normalize_topic_name(topic.name)
    best_bonus = 0.0

    for subject in exam_subjects:
        subject_name = normalize_topic_name(subject.name)
        if subject_name not in topic_name and topic_name not in subject_name:
            continue

        days_left = max((subject.exam_date - today).days, 0)
        if days_left <= 7:
            proximity_factor = 2.5
        elif days_left <= 14:
            proximity_factor = 1.8
        elif days_left <= 30:
            proximity_factor = 1.0
        else:
            proximity_factor = 0.5

        difficulty_factor = DIFFICULTY_FACTORS.get(subject.difficulty.lower(), 1.0)
        best_bonus = max(best_bonus, proximity_factor * difficulty_factor)

    return best_bonus


def _build_topic_state(user_profile, topics, exam_subjects):
    knowledge_level = ""
    if user_profile:
        knowledge_level = (getattr(user_profile, "knowledge_level", "") or "").lower()
    knowledge_factor = KNOWLEDGE_FACTORS.get(knowledge_level, 1.0)
    today = timezone.now().date()

    # First, filter topics by target date and mark overdue ones
    filtered_topics, overdue_topics = _filter_and_update_topics(topics)
    
    # Log overdue topics for visibility
    if overdue_topics:
        print(f"[TIMETABLE] {len(overdue_topics)} overdue topics detected: {[t.name for t in overdue_topics]}")

    state = []
    for topic in filtered_topics:
        remaining = max(0, topic.estimated_minutes - topic.completed_minutes)
        if remaining <= 0:
            continue

        # Calculate deadline bonus from topic's own target_date
        deadline_bonus = _deadline_proximity_bonus(topic, today)
        exam_bonus = _exam_proximity_bonus(topic, exam_subjects, today)
        
        # Non-linear priority: P=5 → 11.2, P=3 → 5.2, P=1 → 1.0
        priority_weight = (topic.priority ** 1.5)
        
        # Score formula: priority + deadline bonus (weighted higher) + exam bonus
        score = (priority_weight * 2.0 + deadline_bonus * 1.5 + exam_bonus) * knowledge_factor
        
        state.append(
            {
                "topic": topic,
                "remaining": remaining,
                "score": score,
                "deadline_bonus": deadline_bonus,
                "is_overdue": topic.is_overdue,
                "last_studied_date": None,
            }
        )

    # Add overdue topics with maximum urgency
    for topic in overdue_topics:
        remaining = max(0, topic.estimated_minutes - topic.completed_minutes)
        if remaining <= 0:
            continue
        
        priority_weight = (topic.priority ** 1.5)
        # Overdue topics get a base score but with high deadline urgency
        score = (priority_weight * 2.0 + 5.0) * knowledge_factor  # 5.0 = max deadline bonus
        
        state.append(
            {
                "topic": topic,
                "remaining": remaining,
                "score": score,
                "deadline_bonus": 5.0,
                "is_overdue": True,
                "last_studied_date": None,
            }
        )

    state.sort(key=lambda row: (-row["score"], row["topic"].id))
    return state


# ---------------------------------------------------------------------------
# Phase 3: Constraint-Enhanced Topic Picker
# ---------------------------------------------------------------------------

def _pick_topic_for_slot(
    topic_state,
    slot_start,
    available,
    max_chunk_minutes,
    daily_usage,
    daily_max,
    last_topic_scheduled,
    global_usage,
    planning_type="SHORT_TERM",
    total_days=1,
    ml_ranker=None,
):
    current_date = slot_start.date()
    iso_week = slot_start.isocalendar()[1]

    valid_candidates = []
    for row in topic_state:
        if row["remaining"] <= 0:
            continue

        topic_id = row["topic"].id

        # Hard cap: daily quota
        if daily_usage.get(topic_id, 0) >= daily_max:
            continue

        # Soft cap: LONG_TERM weekly pacing
        if planning_type == "LONG_TERM" and total_days > 0:
            total_minutes = row["topic"].estimated_minutes
            weekly_cap = max(60, total_minutes // max(1, total_days // 7))
            week_key = (topic_id, iso_week)
            if global_usage.get(week_key, 0) >= weekly_cap:
                continue

        valid_candidates.append(row)

    is_revision = False
    if not valid_candidates and planning_type == "SHORT_TERM":
        # Enable revision mode: ignore remaining constraints to fill empty exam schedule
        is_revision = True
        for row in topic_state:
            if daily_usage.get(row["topic"].id, 0) < daily_max:
                valid_candidates.append(row)

    if not valid_candidates:
        # Fallback: ignore daily cap
        valid_candidates = [r for r in topic_state if r["remaining"] > 0]
        if not valid_candidates and planning_type == "SHORT_TERM":
            valid_candidates = topic_state
            is_revision = True

    if not valid_candidates:
        return None

    best_row = None
    best_key = None

    for row in valid_candidates:
        if is_revision:
            take = min(available, max_chunk_minutes)
        else:
            take = min(available, row["remaining"], max_chunk_minutes)
            
        if take <= 0:
            continue

        predicted_completion = 0.5
        if ml_ranker is not None:
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

        # Cooldown: heavy penalty for back-to-back same-subject scheduling
        if row["topic"] == last_topic_scheduled:
            weighted_score *= 0.15

        # Starvation prevention: boost subjects not studied in 2+ days
        last_day = row.get("last_studied_date")
        if last_day and (current_date - last_day).days >= 2:
            weighted_score *= 2.0

        key = (weighted_score, predicted_completion, -row["topic"].id)
        if best_key is None or key > best_key:
            best_row = row
            best_key = key

    return best_row or valid_candidates[0]


# ---------------------------------------------------------------------------
# Phase 4: Post-Processing Safety Layer
# ---------------------------------------------------------------------------

def _post_process_schedule(entries):
    """Deduplicate and remove overlapping entries before DB commit."""
    seen_sigs = set()
    safe = []
    for entry in entries:
        sig = (entry.topic_id if hasattr(entry, "topic_id") else entry.topic.id,
               entry.start, entry.end)
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        safe.append(entry)

    # Sort and remove overlaps per-user
    safe.sort(key=lambda e: e.start)
    result = []
    last_end = None
    for entry in safe:
        if last_end is not None and entry.start < last_end:
            continue  # Overlap detected — skip
        result.append(entry)
        last_end = entry.end

    return result


# ---------------------------------------------------------------------------
# Core generation loop
# ---------------------------------------------------------------------------

def generate_timetable(
    user_profile,
    topics,
    free_slots,
    exam_subjects,
    max_chunk_minutes=60,
    planning_type="SHORT_TERM",
    ml_ranker=None,
):
    topics = list(topics)
    free_slots = sorted(list(free_slots), key=lambda slot: slot.start)
    exam_subjects = list(exam_subjects)

    if not topics or not free_slots:
        return []

    if user_profile is None:
        return []

    topic_state = _build_topic_state(user_profile, topics, exam_subjects)
    if not topic_state:
        return []

    # Unique dates span tells us how many days the schedule covers
    unique_dates = sorted({s.start.date() for s in free_slots})
    total_days = max(1, (unique_dates[-1] - unique_dates[0]).days + 1)

    entries = []
    daily_usage = {}      # {date: {topic_id: minutes_used, "_max": cap}}
    global_usage = {}     # {(topic_id, iso_week): minutes_used}
    last_topic_scheduled = None

    # Pre-compute daily caps
    for slot in free_slots:
        date = slot.start.date()
        if date not in daily_usage:
            daily_usage[date] = {t.id: 0 for t in topics}
            daily_slots = [s for s in free_slots if s.start.date() == date]
            total_mins = sum((s.end - s.start).total_seconds() // 60 for s in daily_slots)
            max_ratio = 0.65 if len(topics) > 1 else 1.0
            daily_usage[date]["_max"] = max(60, int(total_mins * max_ratio))

    # Main allocation loop
    today = timezone.now().date()
    
    for slot in free_slots:
        slot_start = slot.start
        slot_end = slot.end
        available = int((slot_end - slot_start).total_seconds() // 60)
        date = slot_start.date()
        iso_week = slot_start.isocalendar()[1]

        while available > 0:
            # Find best topic for this slot
            active_topic = _pick_topic_for_slot(
                topic_state=topic_state,
                slot_start=slot_start,
                available=available,
                max_chunk_minutes=max_chunk_minutes,
                daily_usage=daily_usage[date],
                daily_max=daily_usage[date]["_max"],
                last_topic_scheduled=last_topic_scheduled,
                global_usage=global_usage,
                planning_type=planning_type,
                total_days=total_days,
                ml_ranker=ml_ranker,
            )
            
            if not active_topic:
                break
            
            # Check if this topic's deadline has passed
            topic_deadline = _get_topic_deadline(active_topic["topic"], user_profile, exam_subjects)
            if topic_deadline and date >= topic_deadline:
                # This topic's deadline is today or has passed - don't schedule it
                # Remove from consideration and find another topic for this slot
                topic_state = [t for t in topic_state if t["topic"].id != active_topic["topic"].id]
                if not topic_state:
                    break  # No more topics to schedule
                continue  # Try to find another topic for this slot

            if active_topic["remaining"] <= 0:
                take = min(available, max_chunk_minutes)
            else:
                take = min(available, active_topic["remaining"], max_chunk_minutes)
                
            # Determine chapter/sub-topic if curriculum exists
            display_name = active_topic["topic"].name
            curriculum = active_topic["topic"].curriculum
            if curriculum and isinstance(curriculum, list):
                # Simple heuristic: pick chapter based on how many minutes are done
                # This ensures progress through the chapters
                total_est = active_topic["topic"].estimated_minutes
                done = active_topic["topic"].completed_minutes
                # Add the minutes we've scheduled in this run so far to the index
                current_run_done = total_est - active_topic["remaining"]
                effective_done = done + current_run_done
                
                num_chapters = len(curriculum)
                if num_chapters > 0:
                    mins_per_chapter = max(1, total_est // num_chapters)
                    chapter_idx = min(num_chapters - 1, effective_done // mins_per_chapter)
                    display_name = f"{display_name}: {curriculum[chapter_idx]}"

            chunk_end = slot_start + timedelta(minutes=take)
            entries.append(
                TimetableEntry(
                    user=user_profile.user,
                    topic=active_topic["topic"],
                    start=slot_start,
                    end=chunk_end
                )
            )
            # Tag the in-memory object so the view can show the chapter title immediately
            entries[-1].temp_display_name = display_name
            slot_start = chunk_end
            available -= take
            active_topic["remaining"] -= take
            active_topic["last_studied_date"] = date

            # Update usage trackers
            t_id = active_topic["topic"].id
            daily_usage[date][t_id] = daily_usage[date].get(t_id, 0) + take
            week_key = (t_id, iso_week)
            global_usage[week_key] = global_usage.get(week_key, 0) + take
            last_topic_scheduled = active_topic["topic"]

            topic_state.sort(
                key=lambda row: (
                    -row["score"],
                    row["remaining"] == 0,
                    row["topic"].id,
                )
            )

    return entries


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_timetable_for_user(
    user,
    max_chunk_minutes=60,
    include_metadata=False,
    ml_ranker=None,
    use_model_priority=False,
    planning_type="SHORT_TERM",
):
    topics = Topic.objects.filter(user=user)
    free_slots = FreeSlot.objects.filter(user=user)
    exam_subjects = ExamSubject.objects.filter(user=user)
    user_profile = UserProfile.objects.filter(user=user).first()

    # Filter topics by target date and mark overdue ones
    filtered_topics, overdue_topics = _filter_and_update_topics(topics)
    
    # Smart extrapolation with planning horizon awareness (using filtered topics)
    free_slots = _extrapolate_free_slots(
        user, free_slots, filtered_topics, exam_subjects, user_profile, planning_type=planning_type
    )

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
        planning_type=planning_type,
        ml_ranker=ml_ranker,
    )

    if not generated_entries:
        generated_entries = schedule_timetable_for_user(
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
                "planning_type": planning_type,
                "planning_order": ["ml_ranker", "score_weighted_exam_aware", "greedy_fallback"],
                "planner_stage_used": "greedy_fallback",
            }
            if ml_training_meta is not None:
                metadata["ml_training"] = ml_training_meta
            return generated_entries, metadata
        return generated_entries

    # Post-process: deduplicate + overlap check before committing
    clean_entries = _post_process_schedule(generated_entries)

    TimetableEntry.objects.filter(user=user, done=False).delete()
    TimetableEntry.objects.bulk_create(clean_entries)
    saved_entries = TimetableEntry.objects.filter(user=user).select_related("topic").order_by("start")

    if include_metadata:
        metadata = {
            "algorithm": "score_weighted_exam_aware",
            "ai_used": True,
            "ml_ranker_requested": ml_requested,
            "ml_ranker_used": bool(ml_ranker),
            "planning_type": planning_type,
            "entries_before_dedup": len(generated_entries),
            "entries_after_dedup": len(clean_entries),
            "planning_order": ["ml_ranker", "score_weighted_exam_aware", "greedy_fallback"],
            "planner_stage_used": "ml_plus_ai" if ml_ranker else "ai_without_ml",
            "topics_filtered": len(topics) - len(filtered_topics),
            "overdue_topics_count": len(overdue_topics),
            "overdue_topics": [{"name": t.name, "target_date": str(t.target_date)} for t in overdue_topics],
        }
        if ml_training_meta is not None:
            metadata["ml_training"] = ml_training_meta
        return saved_entries, metadata
    return saved_entries
