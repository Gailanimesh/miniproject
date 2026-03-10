# AI Timetable Planner Backend

Django REST Framework backend for conversational timetable planning, onboarding, OCR-based exam parsing, and RAG-style study guidance.

## Stack
- Django 5 + DRF
- SimpleJWT authentication
- SQLite (default)
- SentenceTransformers for embeddings
- Groq chat completion API
- OCR parsing pipeline (`pytesseract` + `Pillow`) with graceful fallback

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

Response 200:
```json
{
  "response": "I have rescheduled your upcoming plan based on your feedback.",
  "tool": "adaptive_reschedule",
  "strategy": {
    "action": "split_topic_into_smaller_sessions",
    "max_chunk_minutes": 30,
    "priority_boost": 1,
    "extra_minutes_ratio": 0.0
  },
  "entries": [
    {
      "id": 88,
      "topic": "Data Structures",
      "start": "2026-03-11T10:00:00Z",
      "end": "2026-03-11T10:30:00Z",
      "done": false
    }
  ],
  "target_entry_id": 52,
  "extra_minutes_added": 0,
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

## Notes
- For OCR in production, install system Tesseract in addition to Python packages.
- If `GROQ_API_KEY` is not configured, chatbot responds with an explicit fallback message.
