import os
import json
import re
import requests
import datetime
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from timetable.models import Topic, FreeSlot


def normalize_topic_name(name):
    """Lowercase, strip specials, basic singularize to avoid duplicates like 'datastructures' vs 'datastructure'."""
    clean = re.sub(r"[^a-z0-9\s]", "", str(name).lower()).strip()
    if clean.endswith("s") and len(clean) > 4 and not clean.endswith("ss"):
        clean = clean[:-1]
    return clean


def determine_time_horizon(user_message=""):
    """Detect planning intent from user message."""
    msg = (user_message or "").lower()
    short_signals = ["exam", "test", "quiz", "deadline", "next week", "monday",
                     "tuesday", "wednesday", "thursday", "friday", "by ", "before "]
    long_signals = ["month", "semester", "year", "3 month", "6 month", "long term"]
    if any(w in msg for w in short_signals):
        return "SHORT_TERM"
    if any(w in msg for w in long_signals):
        return "LONG_TERM"
    return "CONTINUOUS"


def extract_prerequisites_from_chat(user, user_message, conversation=None):
    """
    Uses Groq LLM to extract topics and free slots from natural language.
    Returns dict with 'added_topics', 'added_slots', and 'planning_type'.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {"added_topics": [], "added_slots": [], "planning_type": "SHORT_TERM"}

    history_text = ""
    if conversation:
        messages = conversation.messages.order_by("-timestamp")[:10]
        messages = reversed(messages)
        history_text = "\n".join([f"{msg.sender}: {msg.text}" for msg in messages])

    current_time = timezone.now().isoformat()
    # Explicitly mention the user's likely local offset based on the request context (+05:30 for this user)
    local_offset = "+05:30" 

    system_prompt = f"""You are a JSON data extractor. The current time is {current_time} (UTC).
The user is providing their study topics and/or the daily hours they are free to study.
The user is likely in timezone {local_offset}. 

Extract any NEW topics or free slots mentioned in the latest message.
Rules for slots:
- Return 'start' and 'end' as naive ISO 8601 strings (e.g. "YYYY-MM-DDTHH:MM:SS") WITHOUT timezone suffixes.
- If they say 'from 8pm to 10pm', assume it is {local_offset} local time on {current_time[:10]}.
- IMPORTANT: Return the LOCAL time the user mentioned. Do NOT convert to UTC yourself.
- If they don't specify AM/PM, use logical context (e.g. 2 to 4 usually means 14:00 to 16:00).
Rules for topics:
- Just extract the subject names exactly as mentioned.
- If the user mentions a deadline for a topic (e.g. "Math by April 5", "Physics - deadline April 10"), extract it as target_date.
- Format target_date as "YYYY-MM-DD".
Rules for dates:
- If the user specifies an explicit end date or target date for the schedule (e.g. "until April 2" or "for 5 days"), calculate and return an absolute date.
- If multiple topics have different deadlines, return them as separate topic entries with their individual target_dates.

Return ONLY a raw JSON dictionary exactly matching this schema, with no markdown formatting:
{{
  "topics": [{{
    "name": "string", 
    "estimated_minutes": 120,
    "target_date": "YYYY-MM-DD or null",
    "suggested_curriculum": ["Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4", "Chapter 5"]
  }}],
  "free_slots": [{{"start": "ISO8601 string", "end": "ISO8601 string"}}],
  "exam_date": "YYYY-MM-DD or null"
}}
If no relevant configuration is found, return empty arrays or null.
"""

    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"History:\n{history_text}\n\nLatest message:\n{user_message}"}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }

    # Detect planning horizon from the user's raw message
    planning_type = determine_time_horizon(user_message)

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        data = json.loads(content)

        # Handle explicit end date
        exam_date_str = data.get("exam_date")
        if exam_date_str:
            from users.models import UserProfile
            from django.utils.dateparse import parse_date
            profile_to_update, _ = UserProfile.objects.get_or_create(user=user)
            parsed_date = parse_date(exam_date_str)
            if parsed_date:
                profile_to_update.exam_date = parsed_date
                profile_to_update.save()

        added_topics = []
        for t in (data.get("topics") or []):
            raw_name = t.get("name")
            if not raw_name:
                continue
            # Normalize before DB lookup to prevent near-duplicates
            canonical = normalize_topic_name(raw_name)
            # Find existing topic with same canonical name (case-insensitive)
            existing = Topic.objects.filter(user=user, name__iexact=canonical).first()
            if not existing:
                existing = Topic.objects.filter(user=user, name__iexact=raw_name).first()
            
            # Parse target_date if provided
            target_date_str = t.get("target_date")
            target_date = None
            if target_date_str:
                from django.utils.dateparse import parse_date
                target_date = parse_date(str(target_date_str))
            
            if existing:
                # Update target_date if provided and topic doesn't have one
                if target_date and not existing.target_date:
                    existing.target_date = target_date
                    existing.save(update_fields=["target_date"])
                added_topics.append(existing.name)
            else:
                est = t.get("estimated_minutes")
                try:
                    est = int(est)
                except (TypeError, ValueError):
                    est = 120
                if est <= 0:
                    est = 120
            
                chapters = t.get("suggested_curriculum") or []
                if not isinstance(chapters, list):
                    chapters = []

                topic, created = Topic.objects.get_or_create(
                    user=user,
                    name=canonical,
                    defaults={
                        "estimated_minutes": est,
                        "target_date": target_date,
                    }
                )
                if not created:
                    # Update existing if needed, but don't overwrite user changes
                    if est != 120 and topic.estimated_minutes == 120:
                        topic.estimated_minutes = est
                    if target_date and not topic.target_date:
                        topic.target_date = target_date
                    topic.save(update_fields=["estimated_minutes", "target_date"])
                added_topics.append(topic.name)

        added_slots = []
        for s in (data.get("free_slots") or []):
            start_str = s.get("start")
            end_str = s.get("end")
            if start_str and end_str:
                try:
                    # Parse as naive if no Z/offset, then localize to the user's likely TZ (+05:30)
                    start_dt = parse_datetime(start_str)
                    end_dt = parse_datetime(end_str)
                    
                    if start_dt and end_dt:
                        if timezone.is_naive(start_dt):
                            # Assume +05:30 (India) as the default for this user context
                            tz_offset = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
                            start_dt = timezone.make_aware(start_dt, tz_offset)
                            end_dt = timezone.make_aware(end_dt, tz_offset)
                        
                        # Convert to UTC for storage
                        start_dt = start_dt.astimezone(datetime.timezone.utc)
                        end_dt = end_dt.astimezone(datetime.timezone.utc)
                        
                        # Avoid duplicate base slots on exact same time
                        exists = FreeSlot.objects.filter(user=user, start=start_dt, end=end_dt).exists()
                        if not exists:
                            FreeSlot.objects.create(user=user, start=start_dt, end=end_dt)
                        # Display in local time for feedback (+05:30)
                        local_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
                        l_start = start_dt.astimezone(local_tz)
                        l_end = end_dt.astimezone(local_tz)
                        added_slots.append(f"{l_start.strftime('%I:%M %p')} to {l_end.strftime('%I:%M %p')}")
                except Exception:
                    pass

        return {"added_topics": added_topics, "added_slots": added_slots, "planning_type": planning_type}
    except Exception as e:
        print(f"Extractor Error: {e}")
        return {"added_topics": [], "added_slots": [], "planning_type": planning_type}
