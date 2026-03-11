import os
from datetime import date

import numpy as np
import requests
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from dotenv import load_dotenv
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from timetable.models import ExamSubject, Topic
from users.models import UserProfile
from users.serializers import UserProfileSerializer

from .models import Conversation, Document, Message
from .serializers import ConversationSerializer, MessageSerializer
from .services.feedback_analyzer import adaptive_reschedule_for_user
from .services.ocr_pipeline import extract_text_from_image, parse_exam_timetable
from .services.timetable_generator import generate_timetable_for_user

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
        "model": "openai/gpt-oss-120b",
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


def _choose_rag_context(user_message):
    model = _try_get_embedding_model()
    if not model:
        return ""

    query_embedding = model.encode(user_message).astype(np.float32)
    docs = Document.objects.exclude(embedding=None)

    best_doc = None
    best_score = -1.0

    for doc in docs:
        try:
            doc_embedding = np.frombuffer(doc.embedding, dtype=np.float32)
            denom = np.linalg.norm(query_embedding) * np.linalg.norm(doc_embedding)
            if denom == 0:
                continue
            score = float(np.dot(query_embedding, doc_embedding) / denom)
            if score > best_score:
                best_score = score
                best_doc = doc
        except Exception:
            continue

    return best_doc.content if best_doc else ""


def _upsert_exam_subjects(user, parsed_data):
    created_or_updated = []

    for subject in parsed_data.get("subjects", []):
        name = (subject.get("name") or "").strip()
        raw_date = subject.get("date")
        if not name or not raw_date:
            continue

        try:
            exam_date = date.fromisoformat(raw_date)
        except ValueError:
            continue

        difficulty = (subject.get("difficulty") or "medium").lower()
        obj, _ = ExamSubject.objects.update_or_create(
            user=user,
            name=name,
            exam_date=exam_date,
            defaults={"difficulty": difficulty},
        )
        created_or_updated.append(obj)

        Topic.objects.get_or_create(
            user=user,
            name=name,
            defaults={"estimated_minutes": 120, "priority": 2},
        )

    return created_or_updated


def _serialize_timetable_entry(entry):
    duration_minutes = int((entry.end - entry.start).total_seconds() // 60)
    return {
        "id": entry.id,
        "topic": entry.topic.name,
        "topic_id": entry.topic_id,
        "start": entry.start,
        "end": entry.end,
        "duration_minutes": duration_minutes,
        "done": entry.done,
    }


def _build_timetable_payload(entries, strategy=None, generation_meta=None):
    generation_meta = generation_meta or {}
    ai_used = bool(generation_meta.get("ai_used"))

    payload = {
        "generated_at": timezone.now().isoformat(),
        "algorithm": generation_meta.get("algorithm", "unknown"),
        "ai_used": ai_used,
        "fallback_used": not ai_used,
        "entries": [_serialize_timetable_entry(entry) for entry in entries],
    }

    if strategy is not None:
        payload["max_chunk_minutes"] = strategy.max_chunk_minutes

    reason = generation_meta.get("reason")
    if reason:
        payload["reason"] = reason

    return payload


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


def _handle_timetable_generation(request):
    if not request.data.get("generate_timetable"):
        return None

    entries, generation_meta = generate_timetable_for_user(
        request.user,
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
            parsed_entry_id = int(entry_id)
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


def _handle_ocr_parser(request):
    image = request.FILES.get("exam_image")
    if not image:
        return None

    extracted_text = extract_text_from_image(image)
    parsed = parse_exam_timetable(extracted_text)
    subjects = _upsert_exam_subjects(request.user, parsed)

    return Response(
        {
            "response": "Exam timetable parsed. Please confirm degree, semester and subjects.",
            "tool": "ocr_exam_parser",
            "parsed": parsed,
            "subjects_count": len(subjects),
        },
        status=status.HTTP_200_OK,
    )


def _handle_rag_chat(user_message):
    if not user_message:
        return None

    context = _choose_rag_context(user_message)
    system_prompt = (
        "You are an AI timetable planning assistant. "
        "Prefer concise, actionable study guidance. "
        "Use retrieved context when available. "
        f"Context: {context}"
    )
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

    if user_message:
        Message.objects.create(
            conversation=conversation,
            sender="user",
            text=user_message,
        )

    if bot_message:
        Message.objects.create(
            conversation=conversation,
            sender="bot",
            text=bot_message,
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

        handlers = {
            "onboarding": lambda: _handle_onboarding(request),
            "generate_timetable": lambda: _handle_timetable_generation(request),
            "adaptive_reschedule": lambda: _handle_adaptive_reschedule(request, force=True),
            "ocr_exam_parser": lambda: _handle_ocr_parser(request),
            "rag_chat": lambda: _handle_rag_chat(user_message),
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

        for inferred_tool in [
            _handle_onboarding,
            _handle_ocr_parser,
            _handle_timetable_generation,
            _handle_adaptive_reschedule,
        ]:
            response = inferred_tool(request)
            if response:
                return _persist_messages(request, response, user_message, conversation)

        if user_message:
            response = _handle_rag_chat(user_message)
            return _persist_messages(request, response, user_message, conversation)

        return Response(
            {
                "error": "No actionable input provided.",
                "hint": "Send message/onboarding/generate_timetable/exam_image/tool.",
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
