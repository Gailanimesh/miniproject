# AI Timetable Planner Backend

Django REST Framework backend for conversational timetable planning, onboarding, OCR-based exam parsing, and RAG-style study guidance.

## Stack
- Django 5 + DRF
- SimpleJWT authentication
- SQLite (default)
- SentenceTransformers for embeddings
- Groq chat completion API
- OCR parsing pipeline (`pytesseract` + `Pillow`) with graceful fallback
- Celery scheduled tasks for reminders and auto-rescheduling

## Setup
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

## Core API Endpoints

### Auth
- `POST /api/auth/register/`
- `POST /api/auth/token/`
- `POST /api/auth/token/refresh/`
- `POST /api/auth/logout/`
- `POST /api/auth/password-reset/`
- `POST /api/auth/password-reset/confirm/<uidb64>/<token>/`
- `GET /api/auth/me/`

### Timetable
- `POST /api/timetable/chatbot/`
- `GET /api/timetable/entries/`
- `PATCH /api/timetable/entries/<id>/`
- `POST /api/timetable/entries/<id>/completion-response/`
- `GET /api/timetable/notifications/`
- `PATCH /api/timetable/notifications/<id>/read/`

### Chatbot
- `POST /api/chatbot/converse/`
- `GET /api/chatbot/conversations/`
- `GET /api/chatbot/conversations/<conversation_id>/messages/`

---

## OpenAPI-Style Examples (Frontend Integration)

### `POST /api/chatbot/converse/`
Purpose: Send a conversational message or invoke a tool (`onboarding`, `generate_timetable`, `ocr_exam_parser`, `rag_chat`, `adaptive_reschedule`).

Authentication: `Bearer <access_token>`

#### Example A: RAG chat
Request:
```http
POST /api/chatbot/converse/
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "message": "How should I prepare for data structures in 2 weeks?"
}
```

Response 200:
```json
{
  "response": "Start with arrays and linked lists, then trees, then graphs...",
  "tool": "rag_chat",
  "context_used": true,
  "conversation_id": 14
}
```

#### Example B: Continue an existing conversation
Request:
```json
{
  "conversation_id": 14,
  "message": "Can you make this a daily plan?"
}
```

Response 200:
```json
{
  "response": "Yes. Day 1 and 2 focus on fundamentals...",
  "tool": "rag_chat",
  "context_used": true,
  "conversation_id": 14
}
```

#### Example C: Adaptive rescheduling when a task is missed
Request:
```json
{
  "tool": "adaptive_reschedule",
  "adaptive_reschedule": {
    "entry_id": 52,
    "reason": "I was busy and had no time yesterday"
  }
}
```

---

### `POST /api/timetable/entries/<id>/completion-response/`
Purpose: Respond to post-session reminder notification. Marks entry done if completed, or triggers reschedule if not completed.

Authentication: `Bearer <access_token>`

Request:
```json
{
  "completed": false,
  "response_text": "I could not finish because I was busy",
  "quiz_answer": "I only revised process scheduling"
}
```

Response 200:
```json
{
  "status": "rescheduled",
  "entry_done": false,
  "completion_check": {
    "entry_id": 52,
    "asked_at": "2026-03-11T10:20:00Z",
    "response_received_at": "2026-03-11T10:22:00Z",
    "completed": false,
    "quiz_question": "Quick check for Operating Systems: name one key concept and where you can apply it."
  },
  "strategy": {
    "action": "split_topic_into_smaller_sessions",
    "max_chunk_minutes": 30,
    "priority_boost": 2,
    "extra_minutes_ratio": 0.25
  },
  "generation": {
    "algorithm": "score_weighted_exam_aware",
    "ml_ranker_requested": true,
    "ml_ranker_used": true
  },
  "entries": [
    {
      "id": 88,
      "topic": "Operating Systems",
      "topic_id": 9,
      "start": "2026-03-11T11:00:00Z",
      "end": "2026-03-11T11:30:00Z",
      "done": false
    }
  ]
}
```

Response 200:
```json
{
  "response": "I have rescheduled your upcoming plan based on your feedback.",
  "tool": "adaptive_reschedule",
  "feedback_analysis": {
    "reason": "I was busy and had no time yesterday",
    "signals": {
      "time_constraints": true,
      "fatigue": false,
      "difficulty": false,
      "urgency": false
    },
    "matched_keywords": {
      "time_constraints": ["busy", "no time"],
      "fatigue": [],
      "difficulty": [],
      "urgency": []
    },
    "confidence": "medium"
  },
  "strategy": {
    "action": "split_topic_into_smaller_sessions",
    "max_chunk_minutes": 30,
    "priority_boost": 1,
    "extra_minutes_ratio": 0.0
  },
  "generation": {
    "algorithm": "score_weighted_exam_aware",
    "ai_used": true,
    "ml_ranker_requested": true,
    "ml_ranker_used": true,
    "ml_training": {
      "trained": true,
      "recent_miss_rate": 0.25,
      "training": {
        "source": "synthetic",
        "historical_samples": 0,
        "synthetic_samples": 160,
        "total_samples": 160,
        "epochs": 72,
        "train_loss": 0.552201,
        "val_loss": 0.582911,
        "val_accuracy": 0.75,
        "early_stopped": true,
        "regularization": 0.02,
        "features": [
          "duration_norm",
          "priority_norm",
          "days_until_exam_norm",
          "difficulty_score",
          "hour_norm",
          "weekday_norm",
          "preferred_time_match",
          "recent_miss_rate",
          "remaining_ratio"
        ]
      }
    }
  },
  "timetable": {
    "generated_at": "2026-03-11T10:10:00.100000+00:00",
    "algorithm": "score_weighted_exam_aware",
    "ai_used": true,
    "fallback_used": false,
    "max_chunk_minutes": 30,
    "entries": [
      {
        "id": 88,
        "topic": "Data Structures",
        "topic_id": 7,
        "start": "2026-03-11T10:00:00Z",
        "end": "2026-03-11T10:30:00Z",
        "duration_minutes": 30,
        "done": false
      }
    ]
  },
  "entries": [
    {
      "id": 88,
      "topic": "Data Structures",
      "topic_id": 7,
      "start": "2026-03-11T10:00:00Z",
      "end": "2026-03-11T10:30:00Z",
      "duration_minutes": 30,
      "done": false
    }
  ],
  "target_entry_id": 52,
  "extra_minutes_added": 0,
  "topic_adjustments": {
    "topic_id": 7,
    "topic_name": "Data Structures",
    "before": {
      "priority": 1,
      "estimated_minutes": 120
    },
    "after": {
      "priority": 2,
      "estimated_minutes": 120
    }
  },
  "conversation_id": 14
}
```

---

### `GET /api/chatbot/conversations/`
Purpose: List conversation threads for the authenticated user.

Authentication: `Bearer <access_token>`

Request:
```http
GET /api/chatbot/conversations/
Authorization: Bearer <access_token>
```

Response 200:
```json
[
  {
    "id": 14,
    "started_at": "2026-03-10T08:20:10.101Z",
    "message_count": 12
  },
  {
    "id": 11,
    "started_at": "2026-03-08T13:01:00.310Z",
    "message_count": 5
  }
]
```

---

### `GET /api/chatbot/conversations/<conversation_id>/messages/`
Purpose: Get ordered message history for a single conversation.

Authentication: `Bearer <access_token>`

Request:
```http
GET /api/chatbot/conversations/14/messages/
Authorization: Bearer <access_token>
```

Response 200:
```json
[
  {
    "id": 101,
    "conversation": 14,
    "sender": "user",
    "text": "I missed yesterday's session",
    "timestamp": "2026-03-10T08:22:11.000Z"
  },
  {
    "id": 102,
    "conversation": 14,
    "sender": "bot",
    "text": "No problem. I will reschedule in smaller sessions.",
    "timestamp": "2026-03-10T08:22:11.500Z"
  }
]
```

---

## Run Tests
```bash
python manage.py test
```

## Background Worker (Celery)

Run worker:
```bash
celery -A backend.celery:app worker -l info
```

Run beat scheduler:
```bash
celery -A backend.celery:app beat -l info
```

Scheduled jobs:
- Every minute: pre-reminder + completion-check notification processing
- Daily 00:05: automatic reschedule of stale uncompleted sessions

## Notes
- For OCR in production, install system Tesseract in addition to Python packages.
- If `GROQ_API_KEY` is not configured, chatbot responds with an explicit fallback message.
