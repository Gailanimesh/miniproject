# Product Requirements Document (PRD)

## Project
AI Timetable Planner with Conversational Interface

## Vision
Build a conversational productivity backend where users can plan, generate, and adapt study timetables through chat instead of forms.

## Current Backend Stack
- Django + Django REST Framework
- JWT authentication (SimpleJWT)
- PostgreSQL (Railway deployment)
- SQLite (local development)
- SentenceTransformers + document embedding retrieval for RAG-style responses
- OCR parsing pipeline with Gemini API for image/text extraction
- ML-based timetable prioritization with PyTorch

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
- New `curriculum` JSONField in `Topic` model for AI-generated chapter storage.
- `curriculum` JSONField in `Topic` model for AI-generated chapter storage

### 3. Gated Conversational Flow (Implemented)
- Transitioned from auto-infer to a state-driven prerequisite flow.
- Enforces a logical sequence: **Topics → Slots → Exam Date (Compulsory) → Skip Days (Optional) → Generate**.
- Timetable generation is strictly blocked until an exam date is provided.
- Chatbot proactively asks for the next missing piece of information.

### 4. OCR Exam Timetable Parsing (Enhanced)
- **Multi-branch support**: Detects CE, CS A, CS B, EC, EE, ME branches
- **Branch auto-resolution**: User can specify branch in context (e.g., "I am CS A")
- **Past date detection**: Warns if exam dates are in the past
- **Date correction flow**: User can provide corrected dates in natural language
- **Gemini 2.5 Flash API** integration for intelligent parsing
- `ExamSubject` model stores parsed subjects with exam dates

### 5. AI Curriculum Architect (Implemented)
- Automatic curriculum synthesis for new subjects.
- When a topic is added, the LLM generates a list of 5–8 high-level chapters.
- Timetable entries are labeled with specific chapters (e.g., "Math: Algebra").
- Sequencing logic ensures chapters are covered in a balanced order.

### 6. Conversational Chatbot Endpoint
- Single orchestrator endpoint: `/api/chatbot/converse/`
- Tool-routed behavior via optional `tool` key:
  - `onboarding` - User profile setup
  - `generate_timetable` - Generate study timetable
  - `ocr_exam_parser` - Parse exam timetable from image
  - `rag_chat` - Chat with AI assistant
  - `adaptive_reschedule` - Reschedule on missed tasks
  - `generate_notes_from_conversation` - Generate study notes
- Auto-detection fallback when `tool` is omitted

### 7. Exam-Aware Timetable Generation
- **ML Ranker**: PyTorch-based model for optimal slot selection
- **Score-weighted algorithm**: Considers priority, difficulty, days until exam
- **Exam proximity**: Earlier exams get higher priority
- **Adaptive chunk sizing**: Adjusts session length based on feedback

### 8. Feedback Analysis and Adaptive Rescheduling
- **Keyword detection**: time_constraints, fatigue, difficulty, urgency
- **Strategy building**: Adjusts chunk size, priority boost, extra minutes
- **Auto-reschedule**: Triggers when user misses session without response
- **Quiz prompts**: Generates questions to verify learning

### 9. Notifications System
- Pre-reminder notifications (10 min before)
- Completion check notifications (after session ends)
- Auto-reschedule for pending checks (30 min grace period)
- Notification pipeline service for background processing

### 10. Onboarding Persistence
- `UserProfile` model with fields:
  - `goal_type`, `exam_date`, `knowledge_level`
  - `daily_free_hours`, `skip_days`
  - `occupation`, `preferred_study_time`, `learning_style`

### 11. Conversation History Persistence and Retrieval
- Chatbot persists messages into:
  - `Conversation`
  - `Message`
- Supports continuation through optional `conversation_id`
- `parsed_subjects_for_setup` field for OCR flow continuity
- New history endpoints:
  - `GET /api/chatbot/conversations/`
  - `GET /api/chatbot/conversations/<conversation_id>/messages/`

### 12. RAG-style Chat and Fallback Safety
- Retrieves best matching embedded document context when available
- Calls Groq chat completion API for response generation
- Safe fallback behaviors implemented:
  - missing Groq key -> explicit message response
  - embedding model load failure -> no-context response path
  - missing OCR libs -> empty OCR text path, no crash
  - Gemini API rate limit (429) -> graceful error message

### 13. Testing Status
- **35+ tests passing** across chatbot, timetable, users modules
- E2E workflow tests for complete user journey
- OCR pipeline tests with fixture PDF
- Feedback analysis keyword detection tests
- Timezone conversion tests (IST)

### 14. Study Notes (Stack-like)
- `StudyNote` model for storing AI-generated notes
- Frontend fetches through `/api/chatbot/notes/` endpoint
- Notes created via `generate_notes_from_conversation` tool

### 15. Timezone Handling
- All times stored in UTC in database
- Serialized to IST (+05:30) in API responses
- `TimetableEntrySerializer` and `FreeSlotSerializer` handle conversion

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
- `GET /api/timetable/entries/` - List all entries
- `POST /api/timetable/entries/` - Create entry
- `GET /api/timetable/entries/<id>/` - Get entry
- `PATCH /api/timetable/entries/<id>/` - Update entry
- `POST /api/timetable/entries/<id>/completion-response/` - Mark complete/missed
- `GET /api/timetable/notifications/` - List notifications
- `PATCH /api/timetable/notifications/<id>/read/` - Mark notification read
- `GET /api/timetable/free-slots/` - List free slots
- `POST /api/timetable/free-slots/` - Create free slots

### Chatbot
- `POST /api/chatbot/converse/` - Omni-endpoint for all chatbot tools
- `GET /api/chatbot/conversations/` - List conversations
- `GET /api/chatbot/conversations/<id>/messages/` - Get messages
- `GET /api/chatbot/notes/` - Get study notes

### Users
- `GET /` - Tester HTML interface

---
## Deployment

### Railway
- PostgreSQL database on Railway
- Environment variables:
  - `DATABASE_URL`
  - `SECRET_KEY`
  - `DEBUG=False`
  - `GROQ_API_KEY`
  - `GEMINI_API_KEY`

### Local Development
- SQLite database
- `python manage.py runserver`

---
## Completed Tasks Summary
- ✅ Core auth and user management implemented
- ✅ Timetable models, validations, and baseline scheduler implemented
- ✅ Conversational onboarding implemented
- ✅ OCR parsing pipeline with Gemini API (multi-branch support)
- ✅ ExamSubject and UserProfile models with migrations
- ✅ AI-prioritized timetable generation with ML ranker
- ✅ Adaptive rescheduling for missed tasks with feedback analysis
- ✅ RAG-style chatbot response path implemented
- ✅ Conversation/message persistence implemented
- ✅ Conversation history endpoints implemented
- ✅ Notification pipeline with pre-reminders and completion checks
- ✅ IST timezone handling in serializers
- ✅ Past date detection and correction flow
- ✅ Branch auto-resolution in OCR parsing
- ✅ Tests updated and passing (35+ tests)
- ✅ E2E workflow tests for complete user journey

---
## Priority Next Tasks

### P0 (High Impact)
- [ ] Add quiz/summarization prompts for study verification
- [ ] Add Celery workers for background job processing
- [ ] Add Redis for caching and rate limiting

### P1 (Operational Hardening)
- [ ] Add retry and timeout policies for external LLM/OCR interactions
- [ ] Improve response schema consistency across all chatbot tools
- [ ] Add rate limiting for API endpoints

### P2 (AI Architecture Expansion)
- [ ] Move from DB-only embedding retrieval to FAISS/Chroma vector store
- [ ] Add analytics and feedback loop for plan effectiveness tracking
- [ ] Add more ML training data for better scheduling

---
## Notes
- OCR service uses Gemini 2.5 Flash API (gemini-1.5-flash deprecated)
- Current reminder model exists but active reminder jobs require Celery
- ML ranker uses PyTorch with synthetic training data
