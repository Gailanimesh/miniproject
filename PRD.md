# Product Requirements Document (PRD)

## Project
AI Timetable Planner with Conversational Interface

## Vision
Build a conversational productivity backend where users can plan, generate, and adapt study timetables through chat instead of forms.

## Current Backend Stack
- Django + Django REST Framework
- JWT authentication (SimpleJWT)
- SQLite (current environment)
- SentenceTransformers + document embedding retrieval for RAG-style responses
- OCR parsing pipeline with graceful fallback when OCR libraries are unavailable

---

## Implemented Scope (As of Now)

### 1. Authentication and User Core
- Custom email-based user model
- Register, login, token refresh, logout
- Password reset request + confirm
- Authenticated profile info endpoint (`/api/auth/me/`)

### 2. Timetable Domain
- Topic model with estimated/completed minutes and priority
- FreeSlot model with overlap validation
- TimetableEntry model with completion and notification flags
- Reminder model scaffolded in DB
- Greedy scheduler (`schedule_timetable_for_user`) for baseline planning

### 3. Conversational Chatbot Endpoint
- Single orchestrator endpoint: `/api/chatbot/converse/`
- Tool-routed behavior via optional `tool` key:
  - `onboarding`
  - `generate_timetable`
  - `ocr_exam_parser`
  - `rag_chat`
- Auto-detection fallback when `tool` is omitted:
  - onboarding payload -> onboarding flow
  - `exam_image` -> OCR flow
  - `generate_timetable` flag -> generation flow
  - `message` text -> RAG chat flow

### 4. Onboarding Persistence
- `UserProfile` model implemented and migrated
- Onboarding fields are upserted via chatbot flow:
  - `goal_type`
  - `exam_date`
  - `knowledge_level`
  - `daily_free_hours`
  - `occupation`
  - `preferred_study_time`
  - `learning_style`

### 5. Exam Timetable Parsing + Subject Creation
- OCR extraction service (`extract_text_from_image`)
- Deterministic parser (`parse_exam_timetable`) for date and subject extraction
- `ExamSubject` model implemented and migrated
- Parsed subjects are upserted and linked to user
- Matching topics are auto-created from parsed subject names

### 6. AI-oriented Timetable Generation
- New generator service (`generate_timetable_for_user`) implemented
- Prioritization considers:
  - topic priority
  - exam proximity
  - subject difficulty
  - user knowledge level
- Falls back to baseline greedy scheduler if no AI-generated entries are produced

### 7. Conversation History Persistence and Retrieval
- Chatbot now persists messages into:
  - `Conversation`
  - `Message`
- Supports continuation through optional `conversation_id`
- New history endpoints:
  - `GET /api/chatbot/conversations/`
  - `GET /api/chatbot/conversations/<conversation_id>/messages/`

### 8. RAG-style Chat and Fallback Safety
- Retrieves best matching embedded document context when available
- Calls Groq chat completion API for response generation
- Safe fallback behaviors implemented:
  - missing Groq key -> explicit message response
  - embedding model load failure -> no-context response path
  - missing OCR libs -> empty OCR text path, no crash

### 9. Testing Status
- Full API flow tests are present and passing
- Dedicated chatbot tests cover:
  - onboarding tool
  - timetable generation tool
  - RAG fallback
  - conversation persistence
  - conversation history retrieval

### 10. Adaptive Rescheduling (Implemented)
- New chatbot tool: `adaptive_reschedule`
- Accepts missed entry context and user feedback reason
- Feedback analyzer applies strategy based on reason keywords:
  - split smaller chunks for time constraints
  - increase priority for missed topic
  - add extra estimated minutes for difficult topics
- Regenerates upcoming timetable using adaptive chunk size
- Preserves conversational continuity with `conversation_id`

---

## API Endpoints (Current)

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

## Completed Tasks Summary
- Core auth and user management implemented
- Timetable models, validations, and baseline scheduler implemented
- Conversational onboarding implemented
- OCR parsing pipeline implemented with fail-safe behavior
- ExamSubject and UserProfile models added with migrations
- AI-prioritized timetable generation implemented
- Adaptive rescheduling for missed tasks implemented
- RAG-style chatbot response path implemented
- Conversation/message persistence implemented
- Conversation history endpoints implemented
- Tests updated and passing for core flows

---

## Priority Next Tasks

### P0 (High Impact)
- Add study verification flow (quiz/summarization prompts) and update completion confidence
- Add explicit OCR clarification loop (degree/semester/subject confirmation) before final commit

### P1 (Operational Hardening)
- Add Celery workers and scheduled reminder dispatch using Redis
- Add retry and timeout policies for external LLM/OCR interactions
- Improve response schema consistency across all chatbot tools

### P2 (AI Architecture Expansion)
- Introduce LangGraph agent orchestration for dynamic tool selection
- Move from DB-only embedding retrieval to FAISS/Chroma vector store
- Add analytics and feedback loop for plan effectiveness tracking

---

## Notes
- OCR service code supports graceful fallback, but production OCR quality requires Tesseract runtime installation.
- Current reminder model exists but active reminder jobs are not yet implemented.
