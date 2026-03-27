import re
from datetime import timedelta, date
from collections import defaultdict

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
# Phase 1: Data Integrity & Normalization
# ---------------------------------------------------------------------------

def normalize_topic_name(name):
    """Normalize topic name for matching - extracts code if present."""
    name = str(name).lower().strip()
    
    # Extract subject code pattern like "CST 302" -> "cst302"
    code_match = re.search(r'([a-z]+)\s*(\d+)', name)
    code = ""
    if code_match:
        code = f"{code_match.group(1)}{code_match.group(2)}"
    
    # Clean remaining text
    clean = re.sub(r'[^a-z0-9\s]', ' ', name)
    clean = re.sub(r'\s+', ' ', clean).strip()
    
    # Remove common words
    skip_words = {'by', 'on', 'before', 'due', 'the', 'a', 'an', 'for', 'to', 'and', 'or', 'exam', 'subject', 'paper'}
    words = [w for w in clean.split() if w.lower() not in skip_words and len(w) > 1]
    
    if code:
        return f"{code} {' '.join(words)}".strip()
    return ' '.join(words).strip()


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


# ---------------------------------------------------------------------------
# Phase 2: Exam-Aware Timetable Generation
# ---------------------------------------------------------------------------

def _get_topic_deadline(topic, user_profile, exam_subjects):
    """Get the exam date for a specific topic."""
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


def _filter_active_topics(topics, user_profile, exam_subjects):
    """
    Filter topics that are still active (exam date in future).
    HARD RULE: Never include topics whose deadline has passed.
    """
    today = timezone.now().date()
    active = []
    
    for topic in topics:
        remaining = max(0, topic.estimated_minutes - topic.completed_minutes)
        if remaining <= 0:
            continue  # Skip completed topics
            
        deadline = _get_topic_deadline(topic, user_profile, exam_subjects)
        
        if deadline is None:
            active.append((topic, None, 999))  # No deadline
        elif deadline > today:
            days_left = (deadline - today).days
            active.append((topic, deadline, days_left))
        # Topics with deadline <= today are excluded
    
    # Sort by deadline (earliest first), topics without deadline last
    active.sort(key=lambda x: (x[1] is None, x[1] or date.max, x[0].id))
    
    return [(t, d, dl) for t, d, dl in active if d is not None]  # Only return topics with deadlines


def _extrapolate_free_slots(user, existing_slots, topics, exam_subjects, user_profile,
                             planning_type="SHORT_TERM", max_days=45):
    """Extrapolate free slots until the latest deadline."""
    slots = list(existing_slots)
    if not slots:
        return []

    unique_dates = {s.start.date() for s in slots}

    if len(unique_dates) > 5 or (max(unique_dates) - min(unique_dates)).days > 6:
        return slots

    today = timezone.now().date()

    # Find latest deadline
    latest_deadline = None
    for topic, deadline, _ in _filter_active_topics(topics, user_profile, exam_subjects):
        if deadline and (latest_deadline is None or deadline > latest_deadline):
            latest_deadline = deadline
    
    for subject in exam_subjects:
        if latest_deadline is None or subject.exam_date > latest_deadline:
            latest_deadline = subject.exam_date
    
    if not latest_deadline:
        latest_deadline = today + timedelta(days=30)
    
    # Schedule up to latest deadline + 1 buffer
    target_end_date = min(latest_deadline + timedelta(days=1), today + timedelta(days=max_days))

    first_day = min(unique_dates)
    base_slots = [s for s in slots if s.start.date() == first_day]
    seen_sigs = {(s.start, s.end) for s in slots}

    current_date = first_day + timedelta(days=1)
    while current_date < target_end_date:
        profile_skips = [s.capitalize() for s in getattr(user_profile, "skip_days", []) or []]
        if not profile_skips:
            profile_skips = ["Sunday"]

        current_day_name = current_date.strftime("%A")
        is_skip_day = current_day_name in profile_skips
        
        # Don't skip last 2 days before deadline
        if is_skip_day and planning_type == "SHORT_TERM":
            days_to_deadline = (latest_deadline - current_date).days if latest_deadline else 999
            if days_to_deadline > 2:
                current_date += timedelta(days=1)
                continue

        if current_date not in unique_dates:
            d_diff = (current_date - first_day).days
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
# Phase 3: Exam-Aware Scheduling Algorithm (Strict Priority + Spacing)
# ---------------------------------------------------------------------------

def _exam_aware_schedule(
    user_profile,
    active_topics,  # List of (topic, deadline, days_left)
    free_slots,
    exam_subjects,
    max_chunk_minutes=60,
    planning_type="SHORT_TERM",
    ml_ranker=None,
):
    """
    Strict priority + spacing scheduling algorithm:
    1. Only nearest exam group
    2. Never schedule after exam date
    3. 2 learning + 1 revision per subject
    4. Same-day exams handled fairly
    5. NO back-to-back same subject
    6. NO multiple sessions of same subject in one day if alternatives exist
    """
    today = timezone.now().date()
    free_slots = sorted(free_slots, key=lambda s: s.start)
    
    if not active_topics or not free_slots:
        return []
    
    # Group topics by deadline
    deadline_groups = defaultdict(list)
    for topic, deadline, days_left in active_topics:
        deadline_key = deadline if deadline else date.max
        deadline_groups[deadline_key].append((topic, deadline, days_left))
    
    # Sort deadlines
    sorted_deadlines = sorted(deadline_groups.keys(), key=lambda d: (d is None, d if d else date.max))
    
    # Initialize topic tracking
    topic_schedule = {}
    
    for topic, deadline, days_left in active_topics:
        topic_schedule[topic.id] = {
            "topic": topic,
            "deadline": deadline,
            "days_left": days_left,
            "learning_sessions": 0,
            "revision_sessions": 0,
            "learning_done": False,
            "revision_done": False,
            "all_done": False,
        }
    
    entries = []
    last_topic_id = None
    scheduled_today = set()  # Track subjects scheduled today
    
    # Get unique dates
    unique_dates = sorted(set(s.start.date() for s in free_slots))
    
    for current_date in unique_dates:
        # Reset daily tracking
        scheduled_today = set()
        
        # Find the nearest INCOMPLETE deadline group
        nearest_deadline = None
        for dl in sorted_deadlines:
            if dl is None:
                continue
            if current_date >= dl:
                continue
            group_incomplete = any(
                not topic_schedule[t.id]["all_done"]
                for t, _, _ in deadline_groups[dl]
            )
            if group_incomplete:
                nearest_deadline = dl
                break
        
        if nearest_deadline is None:
            continue
        
        nearest_topics = deadline_groups[nearest_deadline]
        same_day_count = len(nearest_topics)
        
        # Find available subjects from nearest group only
        available_subjects = []
        
        for topic, deadline, days_left in nearest_topics:
            info = topic_schedule[topic.id]
            
            if info["all_done"]:
                continue
            if current_date >= deadline:
                continue
            
            days_to_exam = (deadline - current_date).days
            
            if days_to_exam <= 2 and not info["revision_done"]:
                available_subjects.append((topic.id, info, "revision"))
            elif not info["learning_done"]:
                available_subjects.append((topic.id, info, "learning"))
            elif not info["revision_done"]:
                available_subjects.append((topic.id, info, "revision"))
        
        if not available_subjects:
            continue
        
        # Score for fair selection with spacing priority
        scored_subjects = []
        for topic_id, info, phase in available_subjects:
            deadline_key = info["deadline"]
            days_to_exam = (deadline_key - current_date).days
            
            # Revision has highest priority
            if phase == "revision":
                score = 1000 + (10 - days_to_exam) * 100
            else:
                score = 1.0 / (days_to_exam + 1) * 100
            
            # Same-day fairness: boost underallocated
            if same_day_count > 1:
                total_sessions = info["learning_sessions"] + info["revision_sessions"]
                min_sessions = min(
                    topic_schedule[t.id]["learning_sessions"] + topic_schedule[t.id]["revision_sessions"]
                    for t, _, _ in nearest_topics
                )
                if total_sessions == min_sessions:
                    score *= 1.5
            
            # CRITICAL: Spacing Rule
            # Penalize if already scheduled today
            if topic_id in scheduled_today:
                score *= 0.1  # Heavy penalty for same day repeat
            
            # Penalize back-to-back
            if topic_id == last_topic_id:
                score *= 0.3
            
            scored_subjects.append((topic_id, info, phase, score))
        
        # Sort by score
        scored_subjects.sort(key=lambda x: (-x[3], x[0]))
        
        # Get slots for this day
        day_slots = [s for s in free_slots if s.start.date() == current_date]
        
        for slot in day_slots:
            slot_start = slot.start
            slot_end = slot.end
            available = int((slot_end - slot_start).total_seconds() // 60)
            
            while available > 0 and scored_subjects:
                # CRITICAL ANTI-CLUSTERING: Remove any subject already scheduled today
                available_now = [s for s in scored_subjects if s[0] not in scheduled_today]
                
                # If ALL subjects scheduled today, we have no choice (use shortest session)
                if not available_now:
                     # Allow repeats if we have VERY few subjects or it's urgent
                     available_now = scored_subjects
                
                scored_subjects = available_now
                
                # Re-evaluate scoring with spacing
                for i, (tid, inf, ph, sc) in enumerate(scored_subjects):
                    dtm = (inf["deadline"] - current_date).days
                    if ph == "revision":
                        new_score = 1000 + (10 - dtm) * 100
                    else:
                        new_score = 1.0 / (dtm + 1) * 100
                    
                    # Same-day fairness: boost underallocated
                    if same_day_count > 1:
                        total = inf["learning_sessions"] + inf["revision_sessions"]
                        min_s = min(
                            topic_schedule[t.id]["learning_sessions"] + topic_schedule[t.id]["revision_sessions"]
                            for t, _, _ in nearest_topics
                        )
                        if total == min_s:
                            new_score *= 1.5
                    
                    # Back-to-back penalty
                    if tid == last_topic_id:
                        new_score *= 0.3
                    
                    scored_subjects[i] = (tid, inf, ph, new_score)
                
                scored_subjects.sort(key=lambda x: (-x[3], x[0]))
                
                topic_id, info, phase, score = scored_subjects[0]
                
                # Final validity check
                if info["all_done"] or current_date >= info["deadline"]:
                    scored_subjects.pop(0)
                    continue
                
                days_to_exam = (info["deadline"] - current_date).days
                
                # Determine session type
                if days_to_exam <= 2 and not info["revision_done"]:
                    session_type = "revision"
                elif not info["learning_done"]:
                    session_type = "learning"
                else:
                    session_type = "revision"
                
                take = min(available, max_chunk_minutes)
                
                # Create entry
                chunk_end = slot_start + timedelta(minutes=take)
                display_name = info["topic"].name
                if session_type == "revision":
                    display_name = f"REV: {display_name}"
                
                entries.append(
                    TimetableEntry(
                        user=user_profile.user,
                        topic=info["topic"],
                        start=slot_start,
                        end=chunk_end,
                        session_label=display_name
                    )
                )
                entries[-1].temp_display_name = display_name
                
                # Update tracking
                if session_type == "learning":
                    info["learning_sessions"] += 1
                    if info["learning_sessions"] >= 2:
                        info["learning_done"] = True
                else:
                    info["revision_sessions"] += 1
                    if info["revision_sessions"] >= 1:
                        info["revision_done"] = True
                
                if info["learning_done"] and info["revision_done"]:
                    info["all_done"] = True
                
                topic_schedule[topic_id] = info
                scheduled_today.add(topic_id)  # Mark as scheduled today
                slot_start = chunk_end
                available -= take
                last_topic_id = topic_id
                
                # Remove from available if all done
                if info["all_done"] or info["learning_done"] and info["revision_done"]:
                    scored_subjects.pop(0)
    
    return entries


# ---------------------------------------------------------------------------
# Score helpers (Legacy - kept for compatibility)
# ---------------------------------------------------------------------------

def _deadline_proximity_bonus(topic, today):
    """
    Calculate priority score based on days left until deadline.
    Formula: priority_score = 1 / (days_left + 1)
    Fewer days = HIGHER score (more urgent).
    
    Example: 0 days = 1.0, 1 day = 0.5, 3 days = 0.25, 9 days = 0.1
    """
    if not topic.target_date:
        return 0.0
    
    days_left = max((topic.target_date - today).days, 0)
    
    # Use the formula: 1 / (days_left + 1)
    # This gives higher priority to subjects with fewer days remaining
    return 1.0 / (days_left + 1)


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


def _build_topic_state_for_day(user_profile, topics, exam_subjects, current_date):
    """
    Build topic state for a specific day.
    Only includes topics whose deadline is AFTER current_date.
    Calculates urgency based on days remaining from current_date.
    """
    knowledge_level = ""
    if user_profile:
        knowledge_level = (getattr(user_profile, "knowledge_level", "") or "").lower()
    knowledge_factor = KNOWLEDGE_FACTORS.get(knowledge_level, 1.0)

    state = []
    
    for topic in topics:
        remaining = max(0, topic.estimated_minutes - topic.completed_minutes)
        if remaining <= 0:
            continue

        # Get deadline for this topic
        deadline = _get_topic_deadline(topic, user_profile, exam_subjects)
        
        # HARD RULE: Never schedule on or after deadline
        if deadline and current_date >= deadline:
            continue  # Skip this topic - deadline passed
        
        # Calculate urgency based on days left from current_date
        if deadline:
            days_left = (deadline - current_date).days
            urgency = 1.0 / (days_left + 1)
        else:
            days_left = 999
            urgency = 0.1  # Low priority for topics without deadline
        
        exam_bonus = _exam_proximity_bonus(topic, exam_subjects, current_date)
        priority_weight = topic.priority
        
        # Score: urgency is the PRIMARY factor
        score = (urgency * 100.0) + (priority_weight * 2.0) + exam_bonus
        score *= knowledge_factor
        
        state.append({
            "topic": topic,
            "remaining": remaining,
            "score": score,
            "urgency": urgency,
            "days_left": days_left,
            "deadline": deadline,
            "last_studied_date": None,
        })

    # Sort by urgency (highest first), then by topic id
    state.sort(key=lambda row: (-row["score"], row["topic"].id))
    return state


def _build_topic_state(user_profile, topics, exam_subjects):
    """Legacy function - now calls per-day version with today as date."""
    today = timezone.now().date()
    return _build_topic_state_for_day(user_profile, topics, exam_subjects, today)


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

    # Get current date
    today = timezone.now().date()

    # Get all unique dates and sort them
    unique_dates = sorted({s.start.date() for s in free_slots})
    total_days = max(1, (unique_dates[-1] - unique_dates[0]).days + 1)

    entries = []
    global_usage = {}     # {(topic_id, iso_week): minutes_used}
    last_topic_scheduled = None
    
    # Track remaining minutes for each topic across days
    topic_remaining = {t.id: max(0, t.estimated_minutes - t.completed_minutes) for t in topics}
    topic_deadlines = {t.id: _get_topic_deadline(t, user_profile, exam_subjects) for t in topics}

    # Main allocation loop - rebuild topic state for each day
    for slot in free_slots:
        slot_start = slot.start
        slot_end = slot.end
        available = int((slot_end - slot_start).total_seconds() // 60)
        date = slot_start.date()
        iso_week = slot_start.isocalendar()[1]
        
        # HARD RULE: Skip slots on or after topic deadlines
        # Get valid topics for this day (deadline must be AFTER today)
        valid_topics = []
        for t in topics:
            deadline = topic_deadlines.get(t.id)
            if deadline and date >= deadline:
                continue  # Skip - deadline passed
            if topic_remaining.get(t.id, 0) <= 0:
                continue  # Skip - no remaining work
            valid_topics.append(t)
        
        if not valid_topics:
            continue  # No valid topics for this day
        
        # Build fresh topic state for this day with recalculated urgency
        topic_state = _build_topic_state_for_day(user_profile, valid_topics, exam_subjects, date)
        
        if not topic_state:
            continue
        
        # Calculate daily cap
        daily_slots = [s for s in free_slots if s.start.date() == date]
        total_mins = sum((s.end - s.start).total_seconds() // 60 for s in daily_slots)
        daily_max = max(60, int(total_mins * 0.7))
        
        while available > 0:
            if not topic_state:
                break
            
            # Pick best topic
            active_topic = _pick_topic_for_slot(
                topic_state=topic_state,
                slot_start=slot_start,
                available=available,
                max_chunk_minutes=max_chunk_minutes,
                daily_usage={},  # Not using daily caps anymore for simplicity
                daily_max=daily_max,
                last_topic_scheduled=last_topic_scheduled,
                global_usage=global_usage,
                planning_type=planning_type,
                total_days=total_days,
                ml_ranker=ml_ranker,
            )
            
            if not active_topic:
                break

            take = min(available, active_topic["remaining"], max_chunk_minutes)
            
            # Determine chapter/sub-topic if curriculum exists
            display_name = active_topic["topic"].name
            curriculum = active_topic["topic"].curriculum
            if curriculum and isinstance(curriculum, list):
                total_est = active_topic["topic"].estimated_minutes
                done = active_topic["topic"].completed_minutes
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
                    end=chunk_end,
                    session_label=display_name
                )
            )
            entries[-1].temp_display_name = display_name
            slot_start = chunk_end
            available -= take
            
            # Update remaining
            active_topic["remaining"] -= take
            topic_remaining[active_topic["topic"].id] -= take
            active_topic["last_studied_date"] = date

            # Update usage trackers
            t_id = active_topic["topic"].id
            week_key = (t_id, iso_week)
            global_usage[week_key] = global_usage.get(week_key, 0) + take
            last_topic_scheduled = active_topic["topic"]

            # Re-sort topic state
            topic_state.sort(key=lambda row: (-row["score"], row["remaining"] == 0, row["topic"].id))

    return entries


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_timetable_for_user(
    user,
    max_chunk_minutes=120,
    include_metadata=False,
    ml_ranker=None,
    use_model_priority=False,
    planning_type="SHORT_TERM",
):
    topics = Topic.objects.filter(user=user)
    free_slots = FreeSlot.objects.filter(user=user)
    exam_subjects = ExamSubject.objects.filter(user=user)
    user_profile = UserProfile.objects.filter(user=user).first()

    # Get active topics (only those with future deadlines)
    active_topics = _filter_active_topics(topics, user_profile, exam_subjects)
    
    if not active_topics:
        if include_metadata:
            return [], {"error": "No active topics with future deadlines", "algorithm": "none"}
        return []

    # Extrapolate slots until latest deadline
    free_slots = _extrapolate_free_slots(
        user, free_slots, topics, exam_subjects, user_profile, planning_type=planning_type
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

    # Generate using the exam-aware scheduler
    generated_entries = _exam_aware_schedule(
        user_profile=user_profile,
        active_topics=active_topics,
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
                "planning_order": ["exam_aware", "greedy_fallback"],
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

    # Calculate overdue count
    overdue_count = len([t for t, d, dl in _filter_active_topics(topics, user_profile, exam_subjects) if dl <= 0])

    if include_metadata:
        metadata = {
            "algorithm": "exam_aware",
            "ai_used": True,
            "ml_ranker_requested": ml_requested,
            "ml_ranker_used": bool(ml_ranker),
            "planning_type": planning_type,
            "entries_before_dedup": len(generated_entries),
            "entries_after_dedup": len(clean_entries),
            "planning_order": ["exam_aware", "greedy_fallback"],
            "planner_stage_used": "ml_plus_ai" if ml_ranker else "ai_without_ml",
            "active_topics_count": len(active_topics),
            "overdue_topics_count": overdue_count,
            "subject_order": [
                {"name": t.name, "deadline": str(d), "days_left": dl} 
                for t, d, dl in sorted(active_topics, key=lambda x: (x[1] is None, x[1] or date.max))
            ][:5]
        }
        if ml_training_meta is not None:
            metadata["ml_training"] = ml_training_meta
        return saved_entries, metadata
    return saved_entries
