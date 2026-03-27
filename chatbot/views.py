import os
import re
from datetime import date, datetime

import numpy as np
import requests
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from dotenv import load_dotenv
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from timetable.models import ExamSubject, Topic, FreeSlot
from timetable.serializers import TopicSerializer, FreeSlotSerializer
from users.models import UserProfile
from users.serializers import UserProfileSerializer

from .models import Conversation, Document, Message, StudyNote, generate_conversation_title
from .serializers import ConversationSerializer, MessageSerializer, StudyNoteSerializer
from .services.feedback_analyzer import adaptive_reschedule_for_user
from .services.ocr_pipeline import extract_text_from_image, parse_exam_timetable
from .services.timetable_generator import generate_timetable_for_user
from .services.intent_parser import extract_prerequisites_from_chat

load_dotenv()

EMBEDDING_MODEL = None


def _try_get_embedding_model():
    global EMBEDDING_MODEL
    if EMBEDDING_MODEL is not None:
        return EMBEDDING_MODEL

    try:
        from sentence_transformers import SentenceTransformer

        EMBEDDING_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:
        EMBEDDING_MODEL = False

    return EMBEDDING_MODEL


def _normalize_subject_name(name):
    """
    Normalize subject name for matching.
    - Extract subject code if present (e.g., "CST 302" -> "cst302")
    - Remove special characters
    - Lowercase everything
    - Remove common words
    - Example: "CST 302 Compiler Design" -> "cst302 compiler design"
    """
    import re
    
    name = str(name).strip()
    
    # Extract subject code pattern like "CST 302" or "HUT 300"
    code_match = re.search(r'([A-Za-z]+)\s*(\d+)', name)
    code = ""
    if code_match:
        code = f"{code_match.group(1).lower()}{code_match.group(2)}"
        name = name.replace(code_match.group(0), "").strip()
    
    # Remove special characters, keep letters and spaces
    clean = re.sub(r'[^a-zA-Z0-9\s]', ' ', name)
    clean = re.sub(r'\s+', ' ', clean).strip().lower()
    
    # Remove common words
    skip_words = {'by', 'on', 'before', 'due', 'the', 'a', 'an', 'for', 'to', 'and', 'or', 'exam', 'subject', 'paper'}
    words = [w for w in clean.split() if w.lower() not in skip_words]
    
    # Combine code with cleaned name
    if code:
        return f"{code} {' '.join(words)}".strip()
    return ' '.join(words).strip()


def _validate_exam_dates(parsed_subjects):
    """
    Validate that all exam dates are in the future.
    Returns: {
        'valid': bool,
        'past_dates': list of subjects with past dates,
        'future_dates': list of subjects with valid dates
    }
    """
    from django.utils import timezone
    today = timezone.now().date()
    
    past_dates = []
    future_dates = []
    
    for subj in parsed_subjects:
        date_str = subj.get('date') or subj.get('target_date')
        if not date_str:
            continue
        
        try:
            exam_date = datetime.strptime(str(date_str), "%Y-%m-%d").date()
            if exam_date <= today:
                past_dates.append({
                    'name': subj.get('name', 'Unknown'),
                    'old_date': str(exam_date)
                })
            else:
                future_dates.append({
                    'name': subj.get('name', 'Unknown'),
                    'date': str(exam_date)
                })
        except ValueError:
            continue
    
    return {
        'valid': len(past_dates) == 0,
        'past_dates': past_dates,
        'future_dates': future_dates
    }


def _find_matching_topic(user, normalized_name):
    """
    Find an existing topic that matches the normalized name.
    Uses STRICT matching by subject code first, then word overlap.
    """
    import re
    existing_topics = Topic.objects.filter(user=user)
    
    # Extract code from the input name
    input_code_match = re.search(r'([a-z]+)(\d+)', normalized_name)
    
    # Strategy 1: Match by EXACT subject code (HIGHEST PRIORITY)
    if input_code_match:
        input_code = input_code_match.group(1) + input_code_match.group(2)
        for topic in existing_topics:
            topic_norm = _normalize_subject_name(topic.name)
            topic_code_match = re.search(r'([a-z]+)(\d+)', topic_norm)
            if topic_code_match:
                topic_code = topic_code_match.group(1) + topic_code_match.group(2)
                if input_code.lower() == topic_code.lower():
                    return topic  # Found exact code match
    
    # Strategy 2: Match by code PREFIX only (e.g., "cst" matches "cst302")
    if input_code_match:
        input_prefix = input_code_match.group(1).lower()
        for topic in existing_topics:
            topic_norm = _normalize_subject_name(topic.name)
            topic_code_match = re.search(r'([a-z]+)(\d+)', topic_norm)
            if topic_code_match and topic_code_match.group(1).lower() == input_prefix:
                return topic
    
    # Strategy 3: Match by significant words
    # - If either name has 2+ words, require 2+ matching words
    # - If the shorter name has only 1 word, require at least 1 match
    input_words = [w for w in normalized_name.split() if len(w) > 2 and not w.isdigit()]
    for topic in existing_topics:
        topic_norm = _normalize_subject_name(topic.name)
        topic_words = [w for w in topic_norm.split() if len(w) > 2 and not w.isdigit()]
        common = set(input_words) & set(topic_words)
        
        # Need at least 2 matching words if both have 2+ words
        # Or 1 match if one name is very short (1-2 words)
        min_words = 2 if (len(input_words) >= 2 and len(topic_words) >= 2) else 1
        if len(common) >= min_words:
            return topic
    
    return None


def _cleanup_duplicate_topics(user):
    """
    Clean up duplicate topics for the same user.
    Merges topics that have the same subject code or significant word overlap.
    Returns the number of duplicates removed.
    """
    import re
    topics = list(Topic.objects.filter(user=user))
    removed = 0
    
    # Find duplicates
    to_remove = set()
    
    for i, topic1 in enumerate(topics):
        if topic1.id in to_remove:
            continue
        norm1 = _normalize_subject_name(topic1.name)
        code1 = re.search(r'([a-z]+)(\d+)', norm1)
        
        for topic2 in topics[i+1:]:
            if topic2.id in to_remove:
                continue
            norm2 = _normalize_subject_name(topic2.name)
            code2 = re.search(r'([a-z]+)(\d+)', norm2)
            
            # Check if same code
            if code1 and code2:
                if code1.group(1).lower() == code2.group(1).lower() and code1.group(2) == code2.group(2):
                    # Same subject - keep the one with longer name (usually has code)
                    if len(topic1.name) >= len(topic2.name):
                        to_remove.add(topic2.id)
                    else:
                        to_remove.add(topic1.id)
                    continue
            
            # Check word overlap
            words1 = set(w for w in norm1.split() if len(w) > 2 and not w.isdigit())
            words2 = set(w for w in norm2.split() if len(w) > 2 and not w.isdigit())
            common = words1 & words2
            if len(common) >= 2:
                # Duplicate - keep longer name
                if len(topic1.name) >= len(topic2.name):
                    to_remove.add(topic2.id)
                else:
                    to_remove.add(topic1.id)
    
    # Remove duplicates
    if to_remove:
        removed = len(to_remove)
        Topic.objects.filter(id__in=to_remove).delete()
    
    return removed


def _parse_subject_date_format(user_message, user):
    """
    Parse messages in format: 'Subject - YYYY-MM-DD' or 'Subject: YYYY-MM-DD'
    Also handles natural language: 'Math by April 10', 'Physics on April 15'
    Also handles: 'Subject 2026-03-30'
    
    NORMALIZES subjects to avoid duplicates:
    - "compiler design" and "CST 302 Compiler Design" = same subject
    """
    from users.models import UserProfile
    from dateutil import parser as date_parser
    import dateutil
    
    updated_topics = []
    all_dates = []
    seen_normalized = {}  # Track normalized names to avoid duplicates
    
    # Pattern 1: "Subject - YYYY-MM-DD" or "Subject: YYYY-MM-DD"
    pattern1 = re.compile(r'([a-zA-Z][a-zA-Z0-9\s]*?)\s*[-:]\s*(\d{4}-\d{2}-\d{2})', re.IGNORECASE)
    
    # Pattern 2: "os 2026-03-30" (space between subject and date)
    pattern2 = re.compile(r'([a-zA-Z][a-zA-Z0-9]*)\s+(\d{4}-\d{2}-\d{2})', re.IGNORECASE)
    
    # Pattern 3: "Subject by April 10" or "Subject on April 15" (natural language)
    pattern3 = re.compile(r'([a-zA-Z][a-zA-Z0-9\s]+?)\s+(?:by|on|before|due)\s+([A-Za-z]+\s+\d{1,2}(?:,?\s*\d{4})?)', re.IGNORECASE)
    
    def process_subject_date(subject_name, date_str, seen_dict):
        """Process a subject-date pair and return topic info."""
        subject_name = subject_name.strip()
        date_str = date_str.strip()
        
        try:
            parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            try:
                from dateutil import parser as date_parser
                parsed_date = date_parser.parse(date_str, fuzzy=True).date()
            except:
                return None
        
        # Normalize the subject name
        normalized_name = _normalize_subject_name(subject_name)
        
        # Skip if we've already processed this normalized name
        if normalized_name in seen_dict:
            return None
        
        all_dates.append(parsed_date)
        seen_dict[normalized_name] = True
        
        # Try to find existing topic
        topic = _find_matching_topic(user, normalized_name)
        
        if topic:
            old_date = topic.target_date
            # If existing topic has shorter name but we have a longer name with code, update it
            if len(subject_name) > len(topic.name):
                topic.name = _clean_subject_name(subject_name) or subject_name
            topic.target_date = parsed_date
            topic.save(update_fields=['name', 'target_date'])
            return {
                'name': topic.name,
                'normalized': normalized_name,
                'old_date': str(old_date) if old_date else None,
                'new_date': parsed_date.isoformat(),
                'is_new': False,
                'topic': topic
            }
        else:
            # Prefer the longer name (usually has subject code)
            display_name = _clean_subject_name(subject_name) or subject_name
            # If the original name has a subject code, use it
            if re.search(r'([A-Z]+)\s*(\d+)', subject_name):
                display_name = subject_name.strip()
            topic = Topic.objects.create(
                user=user,
                name=display_name,
                estimated_minutes=120,
                priority=1,
                target_date=parsed_date
            )
            return {
                'name': display_name,
                'normalized': normalized_name,
                'old_date': None,
                'new_date': parsed_date.isoformat(),
                'is_new': True,
                'topic': topic
            }
    
    # Process Pattern 1 and 2 matches
    matches = pattern1.findall(user_message)
    if not matches:
        matches = pattern2.findall(user_message)
    
    for subject_name, date_str in matches:
        result = process_subject_date(subject_name, date_str, seen_normalized)
        if result:
            updated_topics.append(result)
    
    # Also handle natural language patterns like "Math by April 10"
    natural_matches = pattern3.findall(user_message)
    for subject_name, date_str in natural_matches:
        result = process_subject_date(subject_name, date_str, seen_normalized)
        if result:
            updated_topics.append(result)
    
    latest_date = max(all_dates) if all_dates else None
    
    # Cleanup duplicate topics
    duplicates_removed = _cleanup_duplicate_topics(user)
    
    # Validate dates - check for past dates
    validation = _validate_exam_dates(updated_topics)
    
    return {
        'updated_topics': updated_topics,
        'latest_date': latest_date,
        'has_updates': len(updated_topics) > 0,
        'validation': validation,
        'duplicates_removed': duplicates_removed
    }


def _clean_subject_name(name):
    """Remove common words from subject name."""
    import re
    skip_words = {'by', 'on', 'before', 'due', 'the', 'a', 'an', 'for', 'to', 'and', 'or', 'exam', 'subject', 'paper'}
    words = str(name).split()
    cleaned = [re.sub(r'[^a-zA-Z0-9]', '', w) for w in words if w.lower() not in skip_words]
    return ' '.join(cleaned).strip() if cleaned else name.strip()


def call_groq_api(prompt, context, system_prompt):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "Groq API key is not configured."

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        resp_json = response.json()
    except requests.RequestException as exc:
        return f"Groq API error: {exc}"

    if "choices" not in resp_json:
        return "Groq API error: No choices in response."
    return resp_json["choices"][0]["message"]["content"]


def _extract_parent_topic(user_message):
    """
    Extract a clean parent topic from user message.
    Examples:
    - "notes on computer graphics" -> "Computer Graphics"
    - "create notes for this" -> uses conversation context
    """
    if not user_message:
        return "Study Notes"
    
    topic = user_message.strip()
    
    words_to_remove = [
        "create notes", "generate notes", "make notes", "create a note", "create note",
        "notes on", "note on", "notes about", "note about", "on ", "about ",
        "summarize", "summary of", "summary for",
        "explain", "explain about",
        "for this", "of this",
    ]
    
    for phrase in words_to_remove:
        topic = topic.replace(phrase, "")
    
    topic = topic.strip()
    
    if len(topic) < 2:
        return "Study Notes"
    
    return topic[:50]


def _choose_rag_context(query_text):
    model = _try_get_embedding_model()
    if not model:
        return ""

    try:
        query_embed = model.encode([query_text])[0]
    except Exception:
        return ""

    docs = Document.objects.exclude(embedding=None)
    best_doc_content = ""
    best_score = -1.0 # Corrected from 0.3-1.0 to -1.0 for proper comparison

    for doc in docs:
        try:
            doc_embedding = np.frombuffer(doc.embedding, dtype=np.float32)
            # Calculate cosine similarity
            denom = np.linalg.norm(query_embed) * np.linalg.norm(doc_embedding)
            if denom == 0:
                continue
            sim = np.dot(query_embed, doc_embedding) / denom
            if sim > best_score:
                best_score = sim
                best_doc_content = doc.content
        except Exception:
            continue

    return best_doc_content


def _upsert_exam_subjects(user, parsed_data):
    """
    Create or update exam subjects from parsed data.
    Uses subject normalization to prevent duplicates.
    """
    import re
    
    created_or_updated = []
    seen_normalized = {}  # Track normalized names to prevent duplicates

    for subject in parsed_data.get("subjects", []):
        name = (subject.get("name") or "").strip()
        raw_date = subject.get("date")
        if not name or not raw_date:
            continue

        try:
            exam_date = date.fromisoformat(raw_date)
        except ValueError:
            continue

        # Normalize the subject name for duplicate detection
        normalized_name = _normalize_subject_name(name)
        
        # Skip if we've already processed this normalized subject
        if normalized_name in seen_normalized:
            continue
        seen_normalized[normalized_name] = True

        difficulty = (subject.get("difficulty") or "medium").lower()
        obj, _ = ExamSubject.objects.update_or_create(
            user=user,
            name=name,
            exam_date=exam_date,
            defaults={"difficulty": difficulty},
        )
        created_or_updated.append(obj)

        # Try to find existing topic using normalization
        existing_topic = _find_matching_topic(user, normalized_name)
        
        if existing_topic:
            # Update existing topic with longer name if needed
            if len(name) > len(existing_topic.name):
                existing_topic.name = name
            if exam_date:
                existing_topic.target_date = exam_date
            existing_topic.save(update_fields=['name', 'target_date'])
        else:
            # Create new topic - prefer longer name (usually has code)
            Topic.objects.create(
                user=user,
                name=name,
                estimated_minutes=120,
                priority=2,
                target_date=exam_date,
            )

    return created_or_updated


def _parse_corrected_dates(message):
    """
    Parse corrected exam dates from user message.
    Expected format: "Subject Name - 2026-04-15, Subject Name - 2026-04-16"
    Returns list of tuples: [(original_name, new_date)]
    """
    from datetime import datetime
    import re
    
    updated_dates = []
    
    # Split by comma or newline to get individual entries
    entries = re.split(r'[,\n]', message)
    
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        
        # Try different date patterns
        date_patterns = [
            (r'(\d{4}-\d{2}-\d{2})', "%Y-%m-%d"),  # 2026-04-15
            (r'(\d{2}/\d{2}/\d{4})', "%d/%m/%Y"),   # 15/04/2026
            (r'(\d{2}-\d{2}-\d{4})', "%d-%m-%Y"),   # 15-04-2026
        ]
        
        found_date = None
        matched_pattern = None
        
        for pattern, fmt in date_patterns:
            match = re.search(pattern, entry)
            if match:
                date_str = match.group(1)
                try:
                    found_date = datetime.strptime(date_str, fmt).date()
                    matched_pattern = pattern
                    break
                except ValueError:
                    continue
        
        if found_date:
            # Extract subject name (everything before the date pattern)
            date_match = re.search(matched_pattern, entry)
            if date_match:
                subject_name = entry[:date_match.start()].strip().rstrip('-').strip()
                if subject_name:
                    updated_dates.append((subject_name, found_date))
    
    return updated_dates


def _update_exam_dates(user, updated_dates):
    """
    Update exam dates for subjects based on parsed corrections.
    updated_dates is a list of tuples: [(original_name, new_date)]
    
    Generic matching algorithm:
    1. Extract meaningful words from both user input and DB subject names
    2. Match based on shared significant words
    3. No hardcoded course codes or specific department logic
    """
    from users.models import UserProfile
    import re
    
    # Update ExamSubject dates
    exam_subjects = list(ExamSubject.objects.filter(user=user))
    updated_count = 0
    latest_date = None
    
    # Extract meaningful words from user input
    def get_significant_words(text):
        """Extract significant words, skipping course codes like CET, CST, HUT, etc."""
        words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
        skip_words = {'from', 'with', 'this', 'that', 'have', 'been', 'will', 'would', 'could', 'should', 'exam', 'subject', 'course', 'test', 'paper', 'semester'}
        return [w for w in words if w not in skip_words]
    
    # Build matching index from user input
    user_subjects = []  # [(clean_name, significant_words, new_date)]
    for original_name, new_date in updated_dates:
        sig_words = get_significant_words(original_name)
        # Create a normalized name without course codes
        clean_name = ' '.join(sig_words[:3]) if sig_words else original_name
        user_subjects.append({
            'original': original_name,
            'clean': clean_name,
            'words': set(sig_words),
            'date': new_date
        })
    
    # Match each exam subject with user input
    for exam_subj in exam_subjects:
        subj_text = exam_subj.name.lower()
        subj_words = get_significant_words(exam_subj.name)
        subj_word_set = set(subj_words)
        
        best_match = None
        best_score = 0
        
        for user_subj in user_subjects:
            # Calculate match score based on shared significant words
            shared_words = subj_word_set & user_subj['words']
            score = len(shared_words)
            
            # Prefer matches with more shared words
            if score > best_score:
                best_score = score
                best_match = user_subj
        
        # If we found a match with at least 1 significant word
        if best_match and best_score >= 1:
            exam_subj.exam_date = best_match['date']
            exam_subj.save(update_fields=["exam_date"])
            updated_count += 1
            
            if latest_date is None or best_match['date'] > latest_date:
                latest_date = best_match['date']
    
    # Update profile's exam_date to the latest corrected date
    if latest_date:
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.exam_date = latest_date
        profile.save(update_fields=["exam_date"])
    
    return updated_count


IST_OFFSET = timezone.timedelta(hours=5, minutes=30)


def _serialize_timetable_entry(entry):
    duration_minutes = int((entry.end - entry.start).total_seconds() // 60)
    topic_name = getattr(entry, "temp_display_name", entry.topic.name)
    ist_offset = timezone.timedelta(hours=5, minutes=30)
    ist_tz = timezone.get_fixed_timezone(ist_offset)
    return {
        "id": entry.id,
        "topic": topic_name,
        "topic_id": entry.topic_id,
        "start": entry.start.astimezone(ist_tz),
        "end": entry.end.astimezone(ist_tz),
        "duration_minutes": duration_minutes,
        "done": entry.done,
    }


def _build_timetable_payload(entries, strategy=None, generation_meta=None):
    generation_meta = generation_meta or {}
    ai_used = bool(generation_meta.get("ai_used"))

    payload = {
        "generated_at": timezone.now(),
        "algorithm": generation_meta.get("algorithm", "unknown"),
        "ai_used": bool(generation_meta.get("ai_used")),
        "fallback_used": bool(generation_meta.get("fallback_used")),
        "entries": [_serialize_timetable_entry(e) for e in entries],
    }

    if strategy is not None:
        payload["max_chunk_minutes"] = strategy.max_chunk_minutes

    reason = generation_meta.get("reason")
    if reason:
        payload["reason"] = reason

    return payload


def _handle_save_timetable_config(request):
    user = request.user
    topics_data = request.data.get('topics', [])
    free_slots_data = request.data.get('free_slots', [])

    topics_to_create = []
    for t in topics_data:
        serializer = TopicSerializer(data=t, context={'request': request})
        if not serializer.is_valid():
            return Response({"error": "Invalid topic data", "details": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        topics_to_create.append(Topic(user=user, **serializer.validated_data))
    
    if topics_to_create:
        Topic.objects.bulk_create(topics_to_create, ignore_conflicts=True)

    slots_to_create = []
    for fs in free_slots_data:
        fs_serializer = FreeSlotSerializer(data=fs, context={'request': request})
        if not fs_serializer.is_valid():
            return Response({"error": "Invalid free slot data", "details": fs_serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        slots_to_create.append(FreeSlot(user=user, **fs_serializer.validated_data))
    
    if slots_to_create:
        FreeSlot.objects.bulk_create(slots_to_create)

    entries, generation_meta = generate_timetable_for_user(
        user,
        include_metadata=True,
        use_model_priority=True,
    )
    entries = list(entries)
    
    entries_payload = [_serialize_timetable_entry(entry) for entry in entries]
    timetable_payload = _build_timetable_payload(
        entries=entries,
        generation_meta=generation_meta,
    )

    return Response(
        {
            "response": "Timetable configuration saved and new plan generated.",
            "tool": "save_timetable_config",
            "entries": entries_payload,
            "timetable": timetable_payload,
            "generation": generation_meta,
        },
        status=status.HTTP_200_OK,
    )


def _handle_generate_notes_from_conversation(request, conversation):
    user_message = request.data.get("message", "").strip()
    history_text = ""
    
    if conversation:
        messages = conversation.messages.order_by("timestamp")
        if messages.exists():
            history_text = "\n".join([f"{msg.sender}: {msg.text}" for msg in messages])
            
    if user_message:
        history_text += f"\nuser: {user_message}"
        
    if not history_text.strip():
        return Response({"error": "A conversation history or message is required to extract notes.", "tool": "generate_notes_from_conversation"}, status=status.HTTP_400_BAD_REQUEST)
    
    raw_topic = user_message.strip() if user_message else ""
    generic_phrases_exact = [
        "create note", "create notes", "make notes", "generate notes", "save note", "save notes",
        "for this", "of this", "this", "summarize this", "create notes of this",
        "note this", "notes this", "hi", "hello", "hey", "okay", "ok", "yes", "no", "sure", "thanks", "thank you",
        "my name is", "i am", "i'm"
    ]
    generic_prefixes = ["create ", "make ", "generate ", "save ", "hi", "hello", "hey", "my name", "i am", "i'm"]
    
    if raw_topic and raw_topic.lower().strip() not in generic_phrases_exact and not any(raw_topic.lower().startswith(p) for p in generic_prefixes):
        topic_for_notes = _extract_parent_topic(raw_topic)
        system_prompt = (
            f"Generate key study notes on '{topic_for_notes}'.\n\n"
            "STRICT RULES:\n"
            "1. EVERY line must be a complete sentence explaining a concept\n"
            "2. NEVER output headings, titles, or subheadings\n"
            "3. NEVER use bullet points, dashes, asterisks, or numbers at start of lines\n"
            "4. Each line must explain something (minimum 2 sentences worth of info)\n"
            "5. Include the 'why' or 'how' in each explanation\n\n"
            "CORRECT OUTPUT:\n"
            "Binary search works by repeatedly dividing the search interval in half, starting from the middle element, which makes it very efficient for sorted arrays\n"
            "The time complexity is O(log n) because the search space is halved with each comparison, so searching a million elements takes only about 20 comparisons\n\n"
            "WRONG OUTPUT (do not do this):\n"
            "Binary Search Definition\n"
            "Time Complexity: O(log n)\n"
            "- Binary search works by..."
        )
        note_request = f"Generate study notes on {topic_for_notes}"
    else:
        topic_for_notes = None
        if conversation:
            user_msgs = list(conversation.messages.filter(sender="user").order_by("timestamp").values_list('text', flat=True))
            for msg in reversed(user_msgs):
                msg_lower = msg.lower().strip()
                if msg_lower not in generic_phrases_exact and len(msg_lower) > 5:
                    if not any(msg_lower.startswith(p) for p in generic_prefixes):
                        raw_topic = msg.strip()
                        topic_for_notes = _extract_parent_topic(raw_topic)
                        break
        
        if not topic_for_notes:
            topic_for_notes = "Study Notes"
        
        system_prompt = (
            "Extract key study points from this conversation.\n\n"
            "Rules:\n"
            "1. Each line MUST be a complete explanatory point (not just a heading)\n"
            "2. Include WHY or HOW in explanations\n"
            "3. One concept per line, 1-2 sentences\n"
            "4. NO bullet markers, NO headers, NO numbering\n\n"
            "Example (GOOD):\n"
            "Binary search works by repeatedly dividing the search interval in half, starting from the middle element\n"
            "Time complexity is O(log n) because the search space reduces by half with each comparison"
        )
        note_request = f"Conversation:\n{history_text}"
    
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return Response({"error": "GROQ_API_KEY is not configured.", "tool": "generate_notes_from_conversation"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": note_request},
        ],
        "temperature": 0.3,
        "max_tokens": 512,
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    try:
        resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        note_content = resp.json()["choices"][0]["message"]["content"].strip()
        
        lines = note_content.split('\n')
        
        parent_topic = topic_for_notes
        
        created_notes = []
        for line in lines:
            line = line.strip()
            
            if not line or len(line) < 10:
                continue
            
            line = line.strip('*#-•·▪▸:')
            
            if not line or len(line) < 10:
                continue
            
            if any(line.startswith(prefix) for prefix in ['Key ', 'What ', 'How ', 'Why ', 'When ', 'Where ']) and len(line.split()) < 6:
                continue
            
            if line.endswith(':') or line.endswith(':'):
                continue
            
            note_title = line[:150] if len(line) > 150 else line
            
            note = StudyNote.objects.create(
                user=request.user,
                parent_topic=parent_topic,
                topic_title=note_title
            )
            created_notes.append(note)
        
        if not created_notes:
            return Response({
                "response": "I couldn't extract any specific points. Try asking about a specific topic.",
                "tool": "generate_notes_from_conversation",
                "notes_count": 0
            }, status=status.HTTP_200_OK)
        
        serializer = StudyNoteSerializer(created_notes, many=True)
        
        return Response({
            "response": f"Created {len(created_notes)} notes under '{parent_topic}'.",
            "tool": "generate_notes_from_conversation",
            "notes": serializer.data,
            "notes_count": len(created_notes),
            "parent_topic": parent_topic
        }, status=status.HTTP_201_CREATED)
        
    except requests.exceptions.HTTPError as e:
        err_out = str(e)
        if hasattr(e, 'response') and e.response is not None:
            err_out += f" - Response: {e.response.text}"
        return Response({"error": f"Failed to generate notes: {err_out}", "tool": "generate_notes_from_conversation"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        return Response({"error": f"Failed to generate notes: {str(e)}", "tool": "generate_notes_from_conversation"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

def _handle_onboarding(request):
    if not isinstance(request.data.get("onboarding"), dict):
        return None

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    serializer = UserProfileSerializer(
        profile,
        data=request.data["onboarding"],
        partial=True,
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()

    return Response(
        {
            "response": "Onboarding updated successfully.",
            "tool": "onboarding",
            "profile": serializer.data,
            "next": "Share exam timetable text/image or ask to generate timetable.",
        },
        status=status.HTTP_200_OK,
    )


def _handle_timetable_generation(request, force=False, planning_type="SHORT_TERM"):
    has_payload = bool(request.data.get("generate_timetable"))
    user_msg = request.data.get("message", "").lower()
    msg_intent = "generate" in user_msg and ("timetable" in user_msg or "schedule" in user_msg)
    
    if not force and not has_payload and not msg_intent:
        return None

    # ── STRICT PREREQUISITE CHECK ──────────────────────────────────────────
    from timetable.models import Topic, FreeSlot
    from users.models import UserProfile as UP
    
    has_topics = Topic.objects.filter(user=request.user).exists()
    has_slots = FreeSlot.objects.filter(user=request.user).exists()
    profile = UP.objects.filter(user=request.user).first()
    has_exam_date = bool(profile and getattr(profile, "exam_date", None))

    if not has_topics or not has_slots or not has_exam_date:
        missing = []
        if not has_topics: missing.append("subjects")
        if not has_slots: missing.append("study hours")
        if not has_exam_date: missing.append("exam/target date")
        
        p_response = f"I need a few more details before I can generate your timetable. Specifically: **{', '.join(missing)}**."
        if not has_topics:
            p_response = (
                "Sure! Let's build your study plan.\n\n"
                "What subjects are you focusing on?\n"
                "You can also set individual deadlines like:\n"
                "  - 'Math by April 10'\n"
                "  - 'Physics - 2026-04-15'\n"
                "  - 'Maths, Physics, Chemistry'"
            )
        elif not has_slots:
            p_response = (
                "Got your subjects! Now, what time are you free to study each day?\n"
                "(e.g. '8pm to 10pm' or '6pm-10pm on weekdays')"
            )
        elif not has_exam_date:
            p_response = (
                "Slots saved! Finally, what is your exam or target date?\n"
                "Format: 'April 5' or 'OS - 2026-04-10, DS - 2026-04-15'\n"
                "(You can set different dates for each subject!)"
            )

        return Response(
            {
                "response": p_response,
                "tool": "prereq_collect",
                "entries": [],
                "needs_prerequisites": True,
                "missing": missing
            },
            status=status.HTTP_200_OK,
        )

    # Proceed to generation only if all prerequisites are Met
    entries, generation_meta = generate_timetable_for_user(
        request.user,
        include_metadata=True,
        use_model_priority=True,
        planning_type=planning_type,
    )
    entries = list(entries)
    if not entries:
        return Response(
            {
                "response": "Your timetable is currently complete! All active topics have been scheduled or finished.",
                "tool": "generate_timetable",
                "entries": [],
                "generation": generation_meta,
            },
            status=status.HTTP_200_OK,
        )

    entries_payload = [_serialize_timetable_entry(entry) for entry in entries]
    timetable_payload = _build_timetable_payload(
        entries=entries,
        generation_meta=generation_meta,
    )

    return Response(
        {
            "response": "Timetable generated successfully.",
            "tool": "generate_timetable",
            "entries": entries_payload,
            "timetable": timetable_payload,
            "generation": generation_meta,
        },
        status=status.HTTP_200_OK,
    )


def _handle_adaptive_reschedule(request, force=False):
    payload = request.data.get("adaptive_reschedule")
    if isinstance(payload, dict):
        reason = payload.get("reason", "")
        entry_id = payload.get("entry_id")
    elif payload:
        reason = str(payload)
        entry_id = request.data.get("entry_id")
    else:
        reason = request.data.get("reason", "")
        entry_id = request.data.get("entry_id")

    parsed_entry_id = None
    if entry_id not in (None, ""):
        try:
            parsed_entry_id = int(str(entry_id))
        except (TypeError, ValueError):
            return Response(
                {
                    "error": "entry_id must be an integer for adaptive_reschedule.",
                    "tool": "adaptive_reschedule",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    if not reason and parsed_entry_id is None:
        if not force:
            return None
        reason = "missed planned study session"

    result = adaptive_reschedule_for_user(
        user=request.user,
        reason=reason,
        entry_id=parsed_entry_id,
    )

    strategy = result["strategy"]
    analysis = result["analysis"]
    entries = list(result["entries"])
    entries_payload = [_serialize_timetable_entry(entry) for entry in entries]
    generation_meta = result.get("generation_meta", {})
    timetable_payload = _build_timetable_payload(
        entries=entries,
        strategy=strategy,
        generation_meta=generation_meta,
    )
    topic = result.get("topic")

    strategy_payload = (
        strategy.to_dict() if hasattr(strategy, "to_dict") else {
            "action": strategy.action,
            "max_chunk_minutes": strategy.max_chunk_minutes,
            "priority_boost": strategy.priority_boost,
            "extra_minutes_ratio": strategy.extra_minutes_ratio,
        }
    )
    analysis_payload = analysis.to_dict() if hasattr(analysis, "to_dict") else analysis

    response_message = (
        "I have rescheduled your upcoming plan based on your feedback."
        if entries
        else result["message"]
    )

    return Response(
        {
            "response": response_message,
            "tool": "adaptive_reschedule",
            "feedback_analysis": analysis_payload,
            "strategy": strategy_payload,
            "generation": generation_meta,
            "timetable": timetable_payload,
            "entries": entries_payload,
            "topic_adjustments": {
                "topic_id": getattr(topic, "id", None),
                "topic_name": getattr(topic, "name", None),
                "before": result.get("topic_before"),
                "after": result.get("topic_after"),
            },
            "target_entry_id": getattr(result.get("target_entry"), "id", None),
            "extra_minutes_added": result.get("extra_minutes", 0),
        },
        status=status.HTTP_200_OK,
    )


def _resolve_branch_from_text(user_message, branches):
    """
    Tries to map user input to one of the detected branches.
    Uses keyword matching and falls back to Groq LLM.
    """
    if not user_message or not branches:
        return None
        
    msg = user_message.lower().strip()
    
    # 1. Exact or keyword match
    for b in branches:
        b_low = b.lower().strip()
        if b_low == msg or b_low in msg or msg in b_low:
            return b
            
    # 2. LLM Fallback for ambiguous or implicit mentions
    # Example: "i am cs student" matching "CS A"
    prompt = f"User message: '{user_message}'. Detected branches in timetable: {branches}."
    sys_prompt = "You are extracting the chosen branch from a message. Reply with ONLY the exact name of the best matching branch from the list. If none match, reply 'NONE'."
    reply = call_groq_api(prompt, "", sys_prompt)
    candidate = reply.strip().strip('"').strip("'")
    
    if candidate in branches:
        return candidate
    return None


def _handle_stateful_ocr_reply(request, conversation, user_message):
    pending_data = conversation.pending_ocr_data
    branch_subjects = pending_data.get("branch_subjects", {})
    available_branches = list(branch_subjects.keys())
    
    matched_branch = _resolve_branch_from_text(user_message, available_branches)
        
    if matched_branch and matched_branch in branch_subjects:
        subjects_to_save = branch_subjects[matched_branch]
        parsed = {"subjects": subjects_to_save}
        subjects = _upsert_exam_subjects(request.user, parsed)
        
        conversation.pending_ocr_data = None
        conversation.save(update_fields=["pending_ocr_data"])

        # ── Check for past exam dates (mirrors _handle_ocr_parser logic) ──
        from datetime import datetime
        today = datetime.now().date()
        past_dates = []
        for subj in subjects:
            if hasattr(subj, 'exam_date') and subj.exam_date:
                if subj.exam_date <= today:
                    past_dates.append({"name": subj.name, "old_date": str(subj.exam_date)})

        # Store subjects info in conversation so the date-correction state
        # machine at line ~1570 can pick it up on the next message.
        conversation.parsed_subjects_for_setup = {
            "subjects": [
                {"name": s.name, "date": str(s.exam_date) if s.exam_date else None}
                for s in subjects
            ],
            "past_subjects": past_dates,
        }
        conversation.save(update_fields=["parsed_subjects_for_setup"])

        # Build response parts
        subject_list = "\n".join([f"  - {s.name} ({s.exam_date})" for s in subjects[:5]])
        if len(subjects) > 5:
            subject_list += f"\n  - ...and {len(subjects) - 5} more"

        response_parts = [
            f"Exam timetable for **{matched_branch}** parsed and saved!\n\n"
            f"**Your Subjects:**\n{subject_list}"
        ]

        if past_dates:
            past_list = "\n".join([f"  - {p['name']}: {p['old_date']}" for p in past_dates])
            response_parts.append(f"**⚠️ Some exam dates are in the past:**\n{past_list}")
            response_parts.append(
                "Please enter the correct exam dates for these subjects "
                "(format: Subject Name - New Date, e.g., 'Compiler Design - 2026-04-15')"
            )
            needs_past_date_correction = True
        else:
            response_parts.append(
                "**To generate your study timetable, I need to know your free slots.**\n"
                "When are you available to study? (e.g., '7pm to 10pm daily')"
            )
            needs_past_date_correction = False

        response_data = {
            "response": "\n\n".join(response_parts),
            "tool": "ocr_exam_parser",
            "parsed": parsed,
            "subjects_count": len(subjects),
        }
        if needs_past_date_correction:
            response_data["needs_past_date_correction"] = True
        else:
            response_data["needs_free_slots"] = True

        return Response(response_data, status=status.HTTP_200_OK)
    else:
        return Response(
            {
                "response": "I couldn't identify that branch from the timetable. Please ensure you type one of: " + ", ".join(available_branches),
                "tool": "ocr_exam_parser_need_branch",
            },
            status=status.HTTP_200_OK,
        )


def _handle_ocr_parser(request, conversation=None):
    image = request.FILES.get("exam_image")
    if not image:
        return None

    # Create a new conversation if none provided (for OCR uploads)
    if conversation is None:
        conversation = Conversation.objects.create(
            user=request.user,
            title="Exam Timetable Upload"
        )

    user_message = request.data.get("message", "")
    parsed = extract_text_from_image(image, user_prompt=user_message)

    # FALLBACK: If Gemini returned branch_subjects but only ONE branch exists, 
    # we can safely assume it's the correct one even if 'subjects' is empty.
    branch_subjects = parsed.get("branch_subjects", {})
    if not parsed.get("subjects") and len(branch_subjects) == 1:
        single_branch_name = list(branch_subjects.keys())[0]
        parsed["subjects"] = branch_subjects[single_branch_name]
        parsed["has_multiple_branches"] = False # Treat as single branch now

    if parsed.get("has_multiple_branches") and not parsed.get("subjects"):
        detected_branches = parsed.get("detected_branches", [])
        
        # Try to resolve branch from the initial user_message (Context Box)
        resolved_branch = None
        if user_message:
             resolved_branch = _resolve_branch_from_text(user_message, detected_branches)
              
        if resolved_branch and resolved_branch in parsed.get("branch_subjects", {}):
            parsed["subjects"] = parsed["branch_subjects"][resolved_branch]
            # If resolved, we continue to _upsert_exam_subjects as if it were a single branch
        else:
            # Always save pending_ocr_data - conversation is now guaranteed to exist
            conversation.pending_ocr_data = parsed
            conversation.save(update_fields=["pending_ocr_data"])
                
            branches_str = ", ".join(detected_branches)
            return Response(
                {
                    "response": f"I detected multiple branches in this timetable ({branches_str}). Which stream are you in?",
                    "tool": "ocr_exam_parser_need_branch",
                    "conversation_id": conversation.id,
                },
                status=status.HTTP_200_OK,
            )

    subjects = _upsert_exam_subjects(request.user, parsed)

    if conversation and conversation.pending_ocr_data:
        conversation.pending_ocr_data = None
        conversation.save(update_fields=["pending_ocr_data"])

    if not subjects:
        error_msg = parsed.get("error", "I couldn't find any subjects in this timetable. Please make sure the image is clear and contains subject names and dates.")
        return Response(
            {
                "response": f"OCR extraction completed but no subjects were found. {error_msg}",
                "tool": "ocr_exam_parser",
                "parsed": parsed,
                "subjects_count": 0,
                "conversation_id": conversation.id,
            },
            status=status.HTTP_200_OK,
        )

    # Check if exam dates are in the past
    from datetime import datetime
    today = datetime.now().date()
    past_dates = []
    
    for subj in subjects:
        if hasattr(subj, 'exam_date') and subj.exam_date:
            if subj.exam_date < today:
                past_dates.append({"name": subj.name, "old_date": str(subj.exam_date)})
    
    # Store subjects info in conversation for later use
    conversation.parsed_subjects_for_setup = {
        "subjects": [{"name": s.name, "date": str(s.exam_date) if s.exam_date else None} for s in subjects],
        "past_subjects": past_dates,
    }
    conversation.save(update_fields=["parsed_subjects_for_setup"])
    
    # Build user-friendly response
    response_data = {
        "tool": "ocr_exam_parser",
        "parsed": parsed,
        "subjects_count": len(subjects),
        "subjects": [s.name for s in subjects],
        "conversation_id": conversation.id,
    }
    
    # Build the response message
    response_parts = []
    
    # Detect branch from parsed data
    branch_name = parsed.get("detected_branches", [])
    if len(branch_name) == 1:
        response_parts.append(f"Detected you're in **{branch_name[0]}** branch.")
    elif parsed.get("subjects"):
        response_parts.append(f"Found **{len(subjects)} subjects** from your exam timetable.")
    
    # Show subject list
    subject_list = "\n".join([f"  - {s.name} ({s.exam_date})" for s in subjects[:5]])
    if len(subjects) > 5:
        subject_list += f"\n  - ...and {len(subjects) - 5} more"
    response_parts.append(f"**Your Subjects:**\n{subject_list}")
    
    # Check for past dates
    if past_dates:
        past_list = "\n".join([f"  - {p['name']}: {p['old_date']}" for p in past_dates])
        response_parts.append(f"**⚠️ Some exam dates are in the past:**\n{past_list}")
        response_parts.append("Please enter the correct exam dates for these subjects (format: Subject Name - New Date, e.g., 'Compiler Design - 2026-04-15')")
        response_data["response"] = "\n\n".join(response_parts)
        response_data["needs_past_date_correction"] = True
    else:
        # Ask for free slots - use format that triggers free slot extraction
        response_parts.append("What time are you free to study?\n(e.g., '7pm to 10pm daily' or 'Mornings 9am-12pm on weekdays')")
        response_data["response"] = "\n\n".join(response_parts)
        response_data["needs_free_slots"] = True
    
    return Response(response_data, status=status.HTTP_200_OK)


def _handle_auto_setup_from_ocr(request, conversation=None):
    """
    Automatically sets up profile, exam date, creates free slots, and generates timetable
    from parsed OCR data. Uses exam dates from the timetable to calculate study period.
    """
    from datetime import datetime, timedelta
    from timetable.models import Topic, FreeSlot, TimetableEntry
    from users.models import UserProfile
    
    # Get parsed data from conversation or request
    parsed = None
    
    if conversation and conversation.pending_ocr_data:
        parsed = conversation.pending_ocr_data
        conversation.pending_ocr_data = None
        conversation.save(update_fields=["pending_ocr_data"])
    elif request.data.get("parsed"):
        parsed = request.data.get("parsed")
    
    if not parsed:
        return Response(
            {"error": "No parsed timetable data available. Please upload an exam timetable first."},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    subjects = parsed.get("subjects", [])
    if not subjects:
        return Response(
            {"error": "No subjects found in parsed data."},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # 1. Get or create profile
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    
    # 2. Set exam date from latest subject date (last exam = final exam day)
    exam_dates = []
    for s in subjects:
        date_str = s.get("date")
        if date_str:
            try:
                exam_dates.append(datetime.strptime(date_str, "%Y-%m-%d").date())
            except (ValueError, TypeError):
                pass
    
    if exam_dates:
        exam_dates.sort()
        profile.exam_date = exam_dates[-1]  # Last exam date
        profile.days_until_exam = (exam_dates[-1] - datetime.now().date()).days
    
    # 3. Set default profile values
    profile.goal_type = "Internal Exam"
    profile.knowledge_level = "intermediate"
    profile.daily_free_hours = 3
    profile.save()
    
    # 4. Delete existing topics and free slots
    Topic.objects.filter(user=request.user).delete()
    FreeSlot.objects.filter(user=request.user).delete()
    TimetableEntry.objects.filter(user=request.user).delete()
    
    # 5. Create topics with priority based on exam date (earlier exams = higher priority)
    topic_objects = []
    now = datetime.now().date()
    
    for i, subj in enumerate(subjects):
        date_str = subj.get("date")
        exam_date = None
        days_to_exam = 999
        
        if date_str:
            try:
                exam_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                days_to_exam = (exam_date - now).days
            except (ValueError, TypeError):
                pass
        
        # Priority: earlier exams get higher priority (lower number = higher priority)
        priority = max(1, min(10, 10 - (i if i < 10 else 10)))
        
        topic = Topic.objects.create(
            user=request.user,
            name=subj.get("name", "Unknown Subject"),
            estimated_minutes=120,  # 2 hours per subject
            priority=priority,
            difficulty_score=3 if subj.get("difficulty") == "hard" else 2,
        )
        topic_objects.append(topic)
    
    # 6. Create free slots from today until last exam date (7pm-10pm daily)
    today = datetime.now().date()
    days_until_last_exam = (exam_dates[-1] - today).days if exam_dates else 14
    days_until_last_exam = max(1, min(days_until_last_exam, 30))  # Cap at 30 days
    
    start_hour = 19  # 7pm
    end_hour = 22    # 10pm
    
    for i in range(days_until_last_exam):
        day = today + timedelta(days=i)
        FreeSlot.objects.create(
            user=request.user,
            start=datetime.combine(day, datetime.min.time().replace(hour=start_hour)),
            end=datetime.combine(day, datetime.min.time().replace(hour=end_hour))
        )
    
    # 7. Generate timetable with exam-aware scheduling
    entries, generation_meta = generate_timetable_for_user(
        request.user,
        include_metadata=True,
        use_model_priority=True,
        planning_type="SHORT_TERM",
    )
    entries = list(entries)
    
    entries_payload = [_serialize_timetable_entry(entry) for entry in entries]
    timetable_payload = _build_timetable_payload(entries=entries, generation_meta=generation_meta)
    
    # Build subject list with exam dates for response
    subject_list = []
    for subj in subjects:
        subject_list.append(f"{subj.get('name')} (Exam: {subj.get('date', 'TBD')})")
    
    return Response(
        {
            "response": f"Auto setup complete!\n\nFound {len(subjects)} subjects:\n" + "\n".join(subject_list[:5]) + (f"\n...and {len(subjects)-5} more" if len(subjects) > 5 else "") + f"\n\nLast exam: {profile.exam_date}\nDays to prepare: {days_until_last_exam} days\nCreated {days_until_last_exam} days of study slots (7pm-10pm)\nGenerated {len(entries)} timetable entries!",
            "tool": "auto_setup_from_ocr",
            "subjects": subjects,
            "subjects_count": len(subjects),
            "free_slots_created": days_until_last_exam,
            "entries": entries_payload,
            "timetable": timetable_payload,
            "generation": generation_meta,
            "profile": {
                "goal_type": profile.goal_type,
                "exam_date": str(profile.exam_date) if profile.exam_date else None,
                "days_until_exam": days_until_last_exam,
                "daily_free_hours": profile.daily_free_hours,
            }
        },
        status=status.HTTP_200_OK,
    )


def _handle_rag_chat(user_message, conversation=None):
    if not user_message:
        return None

    context = _choose_rag_context(user_message)
    system_prompt = (
        "You are a STRICT validation engine + planner. You are NOT just a timetable generator.\n\n"
        "CORE PRINCIPLE: You are NOT allowed to return a timetable unless it is PERFECT.\n\n"
        "CONTRACT:\n"
        "1. Subject Consistency: Each subject appears ONLY once. Merge duplicates.\n"
        "2. Date Integrity: NEVER modify exam dates.\n"
        "3. Use ALL time slots: No empty slots allowed.\n"
        "4. NO BLOCK SCHEDULING: Forbidden A A A. Required: A B C A B C.\n"
        "5. Round-Robin: Rotate subjects, all appear early.\n"
        "6. NO CLUSTERING: No same subject consecutive or dominating a day.\n"
        "7. REVISION MANDATORY: Each subject needs 2 learning + 1 revision (within 2 days before exam).\n"
        "8. EXAM SAFETY: NEVER schedule on or after exam date.\n"
        "9. SAME-DAY EXAMS: Split sessions equally.\n\n"
        "SELF-AUDIT (CRITICAL):\n"
        "After generating, CHECK:\n"
        "- Subjects count == input count\n"
        "- No duplicate subjects\n"
        "- Dates match input\n"
        "- No clustering\n"
        "- All slots used\n"
        "- Revision exists\n\n"
        "IF ANY CHECK FAILS: DISCARD and REGENERATE silently.\n"
        "ONLY return timetable if ALL checks pass.\n\n"
        f"Study Notes Context: {context}"
    )

    history_text = ""
    if conversation:
        last_messages = conversation.messages.order_by("-timestamp")[:5]
        last_messages = reversed(list(last_messages))
        history_text = "\n".join([f"{msg.sender}: {msg.text}" for msg in last_messages])

    if history_text:
        system_prompt += f"\n\nRecent Conversation History:\n{history_text}"

    reply = call_groq_api(user_message, context, system_prompt)

    return Response(
        {
            "response": reply,
            "tool": "rag_chat",
            "context_used": bool(context),
        },
        status=status.HTTP_200_OK,
    )


def _resolve_conversation(request):
    conversation_id = request.data.get("conversation_id")
    if conversation_id in (None, ""):
        return None, None

    try:
        conversation = Conversation.objects.get(
            id=conversation_id,
            user=request.user,
        )
        return conversation, None
    except (ValueError, TypeError):
        return None, Response(
            {"error": "Invalid conversation_id."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Conversation.DoesNotExist:
        return None, Response(
            {"error": "Conversation not found."},
            status=status.HTTP_404_NOT_FOUND,
        )


def _persist_messages(request, response, user_message, conversation):
    if response.status_code >= 400:
        return response

    if not isinstance(response.data, dict):
        return response

    bot_message = str(response.data.get("response", "")).strip()
    user_message = str(user_message or "").strip()
    if not user_message and not bot_message:
        return response

    if conversation is None:
        conversation = Conversation.objects.create(user=request.user)
        # Auto-generate title from the first user message (one-time, best-effort)
        title = generate_conversation_title(user_message)
        if title:
            conversation.title = title
            conversation.save(update_fields=["title"])

    if user_message:
        Message.objects.create(
            conversation=conversation,
            sender="user",
            text=user_message,
        )

    if bot_message:
        import json
        from django.core.serializers.json import DjangoJSONEncoder
        
        bot_payload = {
            "tool": response.data.get("tool"),
            "entries": response.data.get("entries"),
            "generation": response.data.get("generation"),
        }
        bot_payload = json.loads(json.dumps(bot_payload, cls=DjangoJSONEncoder))
        Message.objects.create(
            conversation=conversation,
            sender="bot",
            text=bot_message,
            payload=bot_payload,
        )

    response.data["conversation_id"] = conversation.id
    return response


class ChatbotConversationView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user_message = request.data.get("message", "")
        if not user_message:
            adaptive_payload = request.data.get("adaptive_reschedule")
            if isinstance(adaptive_payload, dict):
                user_message = adaptive_payload.get("reason", "")
            else:
                user_message = request.data.get("reason", "")
        requested_tool = request.data.get("tool")
        conversation, conversation_error = _resolve_conversation(request)
        if conversation_error:
            return conversation_error

        # ── Step 0: RESET DECISION (Gated early so tool handlers don't bypass) ──
        from timetable.models import Topic, TimetableEntry
        has_topics = Topic.objects.filter(user=request.user).exists()
        has_existing_schedule = TimetableEntry.objects.filter(user=request.user).exists()
        is_generate_intent = any(kw in user_message.lower() for kw in ["generate", "timetable", "schedule"])
        
        if (has_topics or has_existing_schedule) and is_generate_intent:
            # Check if they have ALREADY given the choice in this message
            if not any(kw in user_message.lower() for kw in ["fresh", "previous", "keep", "start over"]):
                 # We only trigger this if it's NOT a direct tool call from the frontend (which usually has its own state)
                 if not requested_tool:
                     reset_q = (
                         "I see you already have a study plan! 📚\n\n"
                         "Would you like to **Start Fresh** (clears everything) or **Use Previous Data** to generate a new timetable?"
                     )
                     return _persist_messages(request,
                         Response({"response": reset_q, "tool": "prereq_collect", "choice_required": True}, status=200),
                         user_message, conversation)

        handlers = {
            "onboarding": lambda: _handle_onboarding(request),
            "generate_timetable": lambda: _handle_timetable_generation(request, force=True),
            "adaptive_reschedule": lambda: _handle_adaptive_reschedule(request, force=True),
            "ocr_exam_parser": lambda: _handle_ocr_parser(request, conversation=conversation),
            "save_timetable_config": lambda: _handle_save_timetable_config(request),
            "generate_notes_from_conversation": lambda: _handle_generate_notes_from_conversation(request, conversation=conversation),
            "rag_chat": lambda: _handle_rag_chat(user_message, conversation=conversation),
            "auto_setup_from_ocr": lambda: _handle_auto_setup_from_ocr(request, conversation=conversation),
        }

        if requested_tool in handlers:
            response = handlers[requested_tool]()
            if response:
                return _persist_messages(request, response, user_message, conversation)
            return Response(
                {
                    "error": f"Tool '{requested_tool}' missing required payload.",
                    "tool": requested_tool,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if conversation and conversation.pending_ocr_data and user_message:
            response = _handle_stateful_ocr_reply(request, conversation, user_message)
            return _persist_messages(request, response, user_message, conversation)
        
        # Handle date correction after OCR
        if conversation and conversation.parsed_subjects_for_setup and user_message:
            past_subjects = conversation.parsed_subjects_for_setup.get("past_subjects", [])
            if past_subjects and "needs_past_date_correction" not in str(request.data):
                # User is providing corrected exam dates
                updated_dates = _parse_corrected_dates(user_message)
                if updated_dates:
                    _update_exam_dates(request.user, updated_dates)
                    # Clear the flag since dates are now corrected
                    conversation.parsed_subjects_for_setup = None
                    conversation.setup_step = "free_slots"
                    conversation.save(update_fields=["parsed_subjects_for_setup", "setup_step"])
                    
                    # Ask for free slots
                    return _persist_messages(request,
                        Response({
                            "response": f"Updated! Your exam dates have been corrected.\n\nWhat time are you free to study?\n(e.g., '7pm to 10pm daily' or 'Mornings 9am-12pm on weekdays')",
                            "tool": "prereq_collect",
                            "next_step": "free_slots",
                        }, status=200),
                        user_message, conversation)

        for inferred_tool in [
            _handle_onboarding,
            lambda req: _handle_ocr_parser(req, conversation=conversation),
            _handle_save_timetable_config if ("topics" in request.data or "free_slots" in request.data) else lambda req: None,
            _handle_timetable_generation,
            _handle_adaptive_reschedule,
            lambda req: _handle_generate_notes_from_conversation(req, conversation) if ("note " in req.data.get("message", "").lower() or ("notes" in req.data.get("message", "").lower())) else None,
        ]:
            response = inferred_tool(request)
            if response:
                return _persist_messages(request, response, user_message, conversation)

        if user_message:
            last_bot_msg = None
            if conversation:
                last_bot_msg = conversation.messages.filter(sender="bot").order_by("-timestamp").first()
            last_bot_text = last_bot_msg.text.lower() if last_bot_msg else ""
            last_bot_tool = last_bot_msg.payload.get("tool") if (last_bot_msg and last_bot_msg.payload) else None

            # ── STATE MACHINE: Gated multi-step prerequisite collection ──────────
            from timetable.models import Topic, FreeSlot
            from users.models import UserProfile as UP

            has_topics = Topic.objects.filter(user=request.user).exists()
            has_slots = FreeSlot.objects.filter(user=request.user).exists()
            profile = UP.objects.filter(user=request.user).first()
            has_exam_date = bool(profile and getattr(profile, "exam_date", None))

            # Detect keywords that signal the user is providing scheduling info
            prereq_keywords = ["free", "pm", "am", ":", "study", "subject", "topic",
                               "generate", "timetable", "schedule", "exam", "until", "date",
                               "week", "monday", "tuesday", "wednesday", "thursday", "friday",
                               "saturday", "sunday", "skip", "off", "holiday", "busy", "none",
                               "fresh", "previous", "start over", "keep", "-", "/", "by", "deadline"]
            # Detect digits as potential dates/times
            is_prereq_message = any(kw in user_message.lower() for kw in prereq_keywords) or any(char.isdigit() for char in user_message)
            is_prereq_state = (last_bot_tool == "prereq_collect")

            # Step 0: RESET DECISION (If topics or timetable exists, ask if they want to start fresh)
            from timetable.models import TimetableEntry
            has_existing_schedule = TimetableEntry.objects.filter(user=request.user).exists()
            is_generate_intent = any(kw in user_message.lower() for kw in ["generate", "timetable", "schedule"])
            
            if (has_topics or has_existing_schedule) and is_generate_intent:
                # Only ask if they haven't ALREADY given the answer in this message
                if "fresh" not in user_message.lower() and "previous" not in user_message.lower() and "keep" not in user_message.lower():
                     reset_q = (
                         "I see you already have a study plan! 📚\n\n"
                         "Would you like to **Start Fresh** (clears everything) or **Use Previous Data** to generate a new timetable?"
                     )
                     return _persist_messages(request,
                         Response({"response": reset_q, "tool": "prereq_collect", "choice_required": True}, status=200),
                         user_message, conversation)

            if "start fresh" in user_message.lower() or "fresh" in user_message.lower():
                Topic.objects.filter(user=request.user).delete()
                FreeSlot.objects.filter(user=request.user).delete()
                from timetable.models import TimetableEntry
                TimetableEntry.objects.filter(user=request.user).delete()
                if profile:
                    profile.exam_date = None
                    profile.skip_days = []
                    profile.save()
                return _persist_messages(request,
                    Response({"response": "Data cleared! Let's start over. What subjects are you focusing on?", "tool": "prereq_collect"}, status=200),
                    user_message, conversation)
            
            if "use previous" in user_message.lower() or "previous data" in user_message.lower() or "keep" in user_message.lower():
                # Carry on with existing data
                if has_topics and has_slots and has_exam_date:
                     response = _handle_timetable_generation(request, force=True)
                     return _persist_messages(request, response, user_message, conversation)
                # Else it will fall through to missing prerequisites
            
            # Step A: If bot just asked for days to skip, parse the answer then generate
            if "any days you'd like to skip" in last_bot_text or (conversation and conversation.setup_step == "skip_days"):
                if conversation:
                    conversation.setup_step = ""  # Clear the step
                    conversation.save(update_fields=["setup_step"])
                
                skip_days = []
                day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
                for d in day_names:
                    if d in user_message.lower():
                        skip_days.append(d.capitalize())

                if not skip_days and ("none" in user_message.lower() or "no" in user_message.lower()):
                     skip_days = profile.skip_days if profile else []
                elif not skip_days and "none" not in user_message.lower():
                     skip_days = profile.skip_days if profile else []

                if profile:
                    profile.skip_days = skip_days
                    profile.save(update_fields=["skip_days"])

                planning_type = "SHORT_TERM" if has_exam_date else "CONTINUOUS"
                response = _handle_timetable_generation(request, force=True,
                                                        planning_type=planning_type)
                if response and isinstance(response.data, dict):
                    skip_note = f"Got it! Skipping {', '.join(skip_days)}. " if skip_days else "No extra skip days noted. "
                    response.data["response"] = skip_note + response.data.get("response", "")
                return _persist_messages(request, response, user_message, conversation)

            # Step B: If bot just asked for the exam date, parse the answer
            if "what is your exam or target date" in last_bot_text or (conversation and conversation.setup_step == "exam_dates"):
                # First, try to parse "Subject - YYYY-MM-DD" format
                subject_dates = _parse_subject_date_format(user_message, request.user)
                
                if subject_dates.get('has_updates'):
                    # Check for past dates
                    validation = subject_dates.get('validation', {})
                    
                    if not validation.get('valid', True):
                        # PAST DATES DETECTED - Ask for corrected dates
                        past = validation.get('past_dates', [])
                        past_list = "\n".join([f"  - {p['name']}: {p['old_date']} (PAST)" for p in past])
                        
                        error_msg = (
                            f"⚠️ I found {len(past)} exam date(s) that are in the past:\n"
                            f"{past_list}\n\n"
                            "Please provide corrected dates for these subjects:\n"
                            "Format: 'Subject Name - YYYY-MM-DD'\n"
                            "Example: 'Math - 2026-04-15'\n\n"
                            "Enter the updated dates:"
                        )
                        return _persist_messages(request,
                            Response({"response": error_msg, "tool": "prereq_collect", "past_dates": past}, status=200),
                            user_message, conversation)
                    
                    # Successfully parsed with VALID dates
                    profile, created = UP.objects.get_or_create(user=request.user)
                    
                    # Update profile exam_date to latest date if provided
                    latest_date = subject_dates.get('latest_date')
                    if latest_date:
                        profile.exam_date = latest_date
                        profile.save(update_fields=['exam_date'])
                    
                    topic_list = []
                    for t in subject_dates['updated_topics']:
                        status_icon = "NEW" if t['is_new'] else "UPDATED"
                        topic_list.append(f"  - {t['name']}: {t['new_date']} ({status_icon})")
                    
                    skip_msg = (
                        f"Got it! I've saved the dates for {len(subject_dates['updated_topics'])} subject(s):\n" + 
                        "\n".join(topic_list) +
                        f"\n\nLast exam: {latest_date}\n\n"
                        "Are there any days you'd like to skip? "
                        "(e.g., 'I'm not free on Sundays', or just say 'none' to continue)"
                    )
                    conversation.setup_step = "skip_days"
                    return _persist_messages(request,
                        Response({"response": skip_msg, "tool": "prereq_collect"}, status=200),
                        user_message, conversation)
                
                # Fall back to LLM extraction
                extract_result = extract_prerequisites_from_chat(request.user, user_message, conversation)
                profile = UP.objects.filter(user=request.user).first()
                has_exam_date = bool(profile and getattr(profile, "exam_date", None))
                if has_exam_date:
                    skip_msg = (
                        "Perfect! I've saved your exam date 🗓️\n\n"
                        "Are there any days you'd like to skip? "
                        "(e.g., 'I'm not free on Sundays', or just say 'none' to continue)"
                    )
                    return _persist_messages(request,
                        Response({"response": skip_msg, "tool": "prereq_collect"}, status=200),
                        user_message, conversation)
                else:
                    return _persist_messages(request,
                        Response({"response": "I couldn't quite catch the date. Please tell me your exam or target date.\nExamples:\n  - 'April 15'\n  - 'OS - 2026-04-10'\n  - 'Math by April 5, Physics - 2026-04-15'", "tool": "prereq_collect"}, status=200),
                        user_message, conversation)

            # Step C: If bot asked for free time slots, extract them
            if "what time are you free to study" in last_bot_text or (conversation and conversation.setup_step == "free_slots"):
                extract_result = extract_prerequisites_from_chat(request.user, user_message, conversation)
                added_s = extract_result.get("added_slots", [])
                if added_s:
                    # Check if subjects already have exam dates (OCR flow)
                    from timetable.models import ExamSubject
                    has_exam_subjects = ExamSubject.objects.filter(user=request.user).exists()
                    
                    if has_exam_subjects:
                        # Skip asking for exam date - go straight to skip days
                        skip_q = (
                            f"I've saved your study window ({', '.join(added_s)}) ✅\n\n"
                            "Are there any days you'd like to skip? "
                            "(e.g., 'I'm not free on Sundays', or just say 'none' to continue)"
                        )
                        return _persist_messages(request,
                            Response({"response": skip_q, "tool": "prereq_collect"}, status=200),
                            user_message, conversation)
                    else:
                        # Ask for exam date (normal onboarding flow)
                        exam_q = (
                            f"I've saved your study window ({', '.join(added_s)}) ✅\n\n"
                            "What is your exam or target date?\n"
                            "You can set:\n"
                            "  - Single date: 'April 15'\n"
                            "  - Per-subject: 'Math - April 10, Physics - April 15'"
                        )
                        return _persist_messages(request,
                            Response({"response": exam_q, "tool": "prereq_collect"}, status=200),
                            user_message, conversation)
                else:
                    return _persist_messages(request,
                        Response({"response": "I didn't catch your free time. Please tell me when you're available to study.\nExamples: '8pm to 10pm', '6pm-10pm daily', '2pm-5pm on weekends'.", "tool": "prereq_collect"}, status=200),
                        user_message, conversation)

            # Step D: If bot asked for subjects, extract them
            if "what subjects are you focusing on" in last_bot_text or (conversation and conversation.setup_step == "subjects"):
                extract_result = extract_prerequisites_from_chat(request.user, user_message, conversation)
                added_t = extract_result.get("added_topics", [])
                if added_t:
                    slot_q = (
                        f"Got it! I've added your topics: **{', '.join(added_t)}** 📚\n\n"
                        "What time are you free to study each day?\n"
                        "(e.g., '8pm to 10pm' or '6pm-10pm daily')"
                    )
                    return _persist_messages(request,
                        Response({"response": slot_q, "tool": "prereq_collect"}, status=200),
                        user_message, conversation)
                else:
                    return _persist_messages(request,
                        Response({"response": "I didn't catch any subjects. Please list the topics you want to study.\nYou can include deadlines like:\n  - 'Math by April 10'\n  - 'Physics - 2026-04-15'\n  - 'Chemistry, Biology'", "tool": "prereq_collect"}, status=200),
                        user_message, conversation)

            # Step E: Fresh intent — try to extract anything provided in this single message
            if is_prereq_message:
                # First, try to parse "Subject - YYYY-MM-DD" format
                subject_dates = _parse_subject_date_format(user_message, request.user)
                
                if subject_dates.get('has_updates'):
                    # Check for past dates FIRST
                    validation = subject_dates.get('validation', {})
                    
                    if not validation.get('valid', True):
                        # PAST DATES DETECTED - Ask for corrected dates
                        past = validation.get('past_dates', [])
                        past_list = "\n".join([f"  - {p['name']}: {p['old_date']} (PAST)" for p in past])
                        
                        error_msg = (
                            f"⚠️ I found {len(past)} exam date(s) that are in the past:\n"
                            f"{past_list}\n\n"
                            "Please provide corrected dates:\n"
                            "Format: 'Subject Name - YYYY-MM-DD'\n"
                            "Example: 'Math - 2026-04-15'\n\n"
                            "Enter the updated dates:"
                        )
                        return _persist_messages(request,
                            Response({"response": error_msg, "tool": "prereq_collect", "past_dates": past}, status=200),
                            user_message, conversation)
                    
                    # Valid dates - continue
                    profile, created = UP.objects.get_or_create(user=request.user)
                    latest_date = subject_dates.get('latest_date')
                    
                    # Update profile exam_date to latest date if provided
                    if latest_date:
                        profile.exam_date = latest_date
                        profile.save(update_fields=['exam_date'])
                    
                    topic_list = []
                    for t in subject_dates['updated_topics']:
                        status_icon = "NEW" if t['is_new'] else "UPDATED"
                        topic_list.append(f"  - {t['name']}: {t['new_date']} ({status_icon})")
                    
                    response_text = (
                        f"Got it! I've saved the dates for {len(subject_dates['updated_topics'])} subject(s):\n" + 
                        "\n".join(topic_list) +
                        f"\n\nLast exam: {latest_date}\n\n"
                    )
                    
                    # Check if we have all prerequisites now
                    has_topics = Topic.objects.filter(user=request.user).exists()
                    has_slots = FreeSlot.objects.filter(user=request.user).exists()
                    
                    if has_topics and has_slots and latest_date:
                        response_text += "Are there any days you'd like to skip? (e.g., 'I'm not free on Sundays', or say 'none' to generate now)"
                        return _persist_messages(request,
                            Response({"response": response_text, "tool": "prereq_collect"}, status=200),
                            user_message, conversation)
                    elif has_topics and not has_slots:
                        response_text += "Now, what time are you free to study each day? (e.g., '8pm to 10pm')"
                        return _persist_messages(request,
                            Response({"response": response_text, "tool": "prereq_collect"}, status=200),
                            user_message, conversation)
                    else:
                        response_text += "What time are you free to study each day? (e.g., '8pm to 10pm')"
                        return _persist_messages(request,
                            Response({"response": response_text, "tool": "prereq_collect"}, status=200),
                            user_message, conversation)
                
                # Fall back to LLM extraction
                extract_result = extract_prerequisites_from_chat(request.user, user_message, conversation)
                added_t = extract_result.get("added_topics", [])
                added_s = extract_result.get("added_slots", [])
                profile = UP.objects.filter(user=request.user).first()
                has_exam_date = bool(profile and getattr(profile, "exam_date", None))
                has_topics = Topic.objects.filter(user=request.user).exists()
                has_slots = FreeSlot.objects.filter(user=request.user).exists()

                # If we have everything needed → ask about skip days then generate
                if has_topics and has_slots and has_exam_date:
                    return _persist_messages(request,
                        Response({"response": "Are there any days you'd like to skip? (e.g., 'I'm not free on Sundays', or say 'none' to generate now)", "tool": "prereq_collect"}, status=200),
                        user_message, conversation)

                # Missing exam date → ask for it next
                if has_topics and has_slots and not has_exam_date:
                    return _persist_messages(request,
                        Response({"response": f"I've saved your info ✅\n\nWhat is your exam or target date? (e.g., 'OS - 2026-04-05' or 'Physics by April 10')", "tool": "prereq_collect"}, status=200),
                        user_message, conversation)

                # Missing slots → ask for free time
                if has_topics and not has_slots:
                    conversation.setup_step = "free_slots"
                    conversation.save(update_fields=["setup_step"])
                    return _persist_messages(request,
                        Response({"response": f"Topics saved 📚 Now, what time are you free to study each day? (e.g., '8pm to 10pm')", "tool": "prereq_collect"}, status=200),
                        user_message, conversation)

                # Fallback: Treat as RAG if nothing clear was found, BUT if we are in a prereq state, try one last extract as a catch-all
                if not (has_topics or has_slots or has_exam_date):
                     # If they said something completely random while we were waiting for subjects, let RAG handle it
                     pass

            if is_prereq_state or is_prereq_message:
                # If we're here, it means the specific step matching didn't yield a return
                # We do a final extraction pass to see if we can move the needle
                extract_result = extract_prerequisites_from_chat(request.user, user_message, conversation)
                if extract_result.get("added_topics") or extract_result.get("added_slots"):
                     # Re-run logic to see where we stand
                     profile = UP.objects.filter(user=request.user).first()
                     has_exam_date = bool(profile and getattr(profile, "exam_date", None))
                     has_topics = Topic.objects.filter(user=request.user).exists()
                     has_slots = FreeSlot.objects.filter(user=request.user).exists()
                     
                     if has_topics and has_slots and has_exam_date:
                         return _persist_messages(request,
                             Response({"response": "I've updated your info! Ready to generate? Or any days to skip?", "tool": "prereq_collect"}, status=200),
                             user_message, conversation)
                     # (Essentially re-trigger the E Logic but safer)

            response = _handle_rag_chat(user_message, conversation=conversation)
            return _persist_messages(request, response, user_message, conversation)

        return Response(
            {
                "error": "No actionable input provided.",
                "hint": "Send message/onboarding/generate_timetable/exam_image/tool.",
                "help": {
                    "topics": "List your subjects: 'Math, Physics, Chemistry' or with deadlines: 'Math by April 10, Physics - 2026-04-15'",
                    "slots": "Set free time: '8pm to 10pm' or '6pm-10pm weekdays'",
                    "exam_date": "Set date: 'April 5' or per-subject: 'OS - 2026-04-10, DS - 2026-04-15'"
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )


class ConversationListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ConversationSerializer

    def get_queryset(self):
        return (
            Conversation.objects.filter(user=self.request.user)
            .annotate(message_count=Count("messages"))
            .order_by("-started_at")
        )


class ConversationMessagesView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MessageSerializer

    def get_queryset(self):
        conversation = get_object_or_404(
            Conversation,
            id=self.kwargs["conversation_id"],
            user=self.request.user,
        )
        return conversation.messages.order_by("timestamp")


class StudyNoteListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StudyNoteSerializer

    def get_queryset(self):
        return StudyNote.objects.filter(user=self.request.user).order_by("-created_at")


class StudyNoteDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StudyNoteSerializer

    def get_queryset(self):
        return StudyNote.objects.filter(user=self.request.user)
