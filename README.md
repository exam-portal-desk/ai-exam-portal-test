# ExamPortal — AI-Powered Online Examination Platform

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-000000?style=flat-square&logo=flask&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-database-4169E1?style=flat-square&logo=postgresql&logoColor=white)
![Socket.IO](https://img.shields.io/badge/Socket.IO-realtime-010101?style=flat-square&logo=socketdotio&logoColor=white)
![Bootstrap](https://img.shields.io/badge/Bootstrap-5.3-7952B3?style=flat-square&logo=bootstrap&logoColor=white)

**A full-stack examination platform for institutions and coaching centres.**
Secure exam delivery · AI question generation · Real-time analytics · Student notebooks · Study assistant

</div>

---

## Overview

ExamPortal is a Flask web application covering the full exam lifecycle: administrators organize exams under a category → subcategory hierarchy, build question banks manually, in bulk, via CSV, or with AI generation from PDFs/images, and control exactly when results become visible. Students take exams in a monitored, distraction-free interface, review detailed per-question results, track their ranking, keep private notebooks, and get AI-assisted explanations and study help.

Every external dependency — database, object storage, email, and AI models — is accessed through a provider-independent abstraction, so swapping providers is a configuration change, not a code change.

---

## Key Features

### Exam Management
- Category → Subcategory → Exam hierarchy for organizing content
- MCQ, MSQ (multi-select), and Numeric question types
- Manual single-question entry, batch add, or CSV bulk import/export
- Full LaTeX editor for mathematical/scientific question authoring
- Per-question image attachments and per-question source/previous-year tags
- Positive/negative marking with per-question overrides, max attempts per student

### AI Capabilities
All AI calls are routed through a config-driven model registry (`config/ai_models.json`) rather than hardcoded to one vendor — each flow below can independently point at Groq or Gemini (or any OpenAI-compatible/Gemini-compatible endpoint) purely via configuration.
- **AI Question Generator** — upload a PDF or image set and generate MCQ/numeric questions, reviewed and edited in the same CSV-style editor used for bulk import before saving
- **AI Study Assistant** — chat interface for exam preparation with LaTeX-aware responses and daily usage limits
- **AI Explanations** — Chain-of-Thought explanations for exam questions, including image-based (vision model) questions, with a persisted per-question explanation history

### Secure Exam Delivery
- Fullscreen enforcement with violation monitoring and tab-switch/visibility-change detection
- Server-side exam session state with auto-submit on timer expiry
- Progressive answer sync during the exam to survive connection drops

### Result Control
Per-exam result visibility: **instant** (visible on submission), **delayed** (released after a configurable window), or **manual** (admin-triggered release for the whole cohort).

### Analytics & Ranking
- Student-level performance analytics and exam history
- Live percentile-based ranking/leaderboard per exam
- Full response review with correct-answer comparison and image support
- Admin-side cross-student and cross-exam analytics dashboards, PDF result export

### Notes
Private student notebooks with a canvas-based page editor, drawing tools, a notebook library with sharing, trash with configurable retention, and export.

### Communication
- Peer-to-peer and group chat over Socket.IO, with connection requests, presence, unread badges, and per-conversation visibility control
- Per-question discussion threads with replies, pinning, and best-answer marking

### Access Requests & User Management
Students can request elevated access; admins approve/deny with a recorded reason. Every request/decision is kept as an append-only audit trail. Removing a request from the list soft-deletes it (hidden, never erased) so audit history is never lost, and never touches the affected user's role.

### Authentication & Security
- Email/password login and **Sign in with Google** (Authlib/OpenID Connect), including automatic linking of an existing email account to a Google identity
- Passwords hashed with bcrypt; password history prevents reusing recent passwords
- Single active session per account, enforced with an email OTP challenge when a second login is attempted
- Login attempt rate limiting with temporary lockout
- Optional JWT bearer-token authentication layered on top of the normal cookie session, for non-browser/API clients — opt-in and fully backward compatible with existing session-based routes

---

## Architecture

```
Browser (Bootstrap 5, vanilla JS, Socket.IO client, MathJax/KaTeX, Chart.js)
        │  HTTP + WebSocket
        ▼
Flask application (main.py → app/__init__.py, gevent worker)
├─ app/routes/web/       Page routes (server-rendered HTML)
├─ app/routes/api/v01/   JSON API routes consumed by page JS
├─ app/services/         Business logic (exam scoring, AI, email, PDF, ranking, ...)
├─ app/db/               One module per DB domain — all SQL lives here
├─ app/storage/          Object storage abstraction (local disk or S3-compatible)
├─ app/middleware/       Session guard decorators, hybrid JWT auth
└─ app/utils/            Shared helpers (datetime, LaTeX, cache, sanitize, pagination)
        │
        ▼
PostgreSQL (via a pooled psycopg2 connection — any Postgres provider)
```

Both `app/routes/web/` and `app/routes/api/v01/` are split into a flat set of feature modules (auth, exams, questions, chat, notes, ...) plus an `admin/` subpackage for admin-only routes — web modules render templates, API modules return JSON for the same page's AJAX calls.

---

## Technology Stack

| Layer | Technology |
|---|---|
| **Backend framework** | Python 3.11+, Flask 3.1 |
| **Real-time** | Flask-SocketIO, gevent + gevent-websocket |
| **Database** | PostgreSQL via `psycopg2` (pooled connections) — provider-independent |
| **Sessions** | Flask-Session (server-side, filesystem-backed by default) |
| **Password hashing** | bcrypt |
| **Google OAuth** | Authlib (Sign in with Google / OpenID Connect) |
| **API auth** | Custom hybrid JWT middleware, opt-in alongside cookie sessions |
| **AI** | Config-driven model registry (`config/ai_models.json`); calls Groq and Google Gemini directly over HTTP — no vendor SDK |
| **Object storage** | Local filesystem, or any S3-compatible provider via `boto3` |
| **Email** | Generic HTTP email API — provider-independent, no vendor SDK |
| **PDF** | ReportLab (generation), pypdf (parsing) |
| **Data processing** | pandas, numpy (CSV import/export) |
| **Frontend** | Bootstrap 5.3, vanilla JavaScript (ES6+), Socket.IO client |
| **Math rendering** | MathJax 3, KaTeX, latex2mathml |
| **Charts** | Chart.js |
| **Serialization** | orjson |
| **Deployment** | Gunicorn + `GeventWebSocketWorker`, deployed on Render |

---

## Project Structure

```
ExamPortal/
├── main.py                    # Entry point — gevent monkey-patch, app factory, socketio.run()
├── requirements.txt
├── config/
│   └── ai_models.json         # AI provider/model registry (text + vision models)
├── migrations/                 # Incremental SQL migrations (applied manually)
│
├── app/
│   ├── __init__.py            # App factory: sessions, SocketIO, OAuth, JWT, blueprints
│   ├── config.py              # All environment variables read here, nowhere else
│   │
│   ├── routes/
│   │   ├── web/                # Server-rendered page routes
│   │   │   ├── admin/          # Admin-only page routes
│   │   │   └── *.py             # auth, exams, dashboard, chat, notes, results, ...
│   │   └── api/v01/            # JSON API routes (versioned)
│   │       └── admin/          # Admin-only API routes
│   │
│   ├── services/               # Business logic — exam scoring, AI, email, PDF, ranking,
│   │                            # notes, chat, discussions, auth, user deletion
│   ├── db/                     # One module per DB domain (users, exams, questions, ...)
│   ├── storage/                # Object storage backends: local.py, s3.py
│   ├── middleware/              # session_guard.py, jwt_middleware.py
│   └── utils/                  # datetime_service, latex, cache, sanitize, pagination
│
├── templates/
│   ├── admin/                  # Admin portal pages
│   ├── notes/                  # Notebook editor/library/trash
│   ├── emails/                 # Transactional email templates
│   ├── partials/               # Shared Jinja fragments
│   └── *.html                  # Student-facing pages (dashboard, exam, results, chat, ...)
│
└── static/
    ├── shared/                 # Reusable UI components (date-picker, view-toggle, ...)
    ├── notes/                  # Notebook editor JS/CSS (drawing tools, export)
    ├── ai_assistant/           # Study assistant chat UI
    ├── admin/                  # Admin list-controller UI
    └── theme.css                # Multi-theme design system (dark/light + variants)
```

---

## Setup

### Prerequisites
- Python 3.11+
- A PostgreSQL database (Supabase, Render Postgres, or self-hosted — any provider works, only `DATABASE_URL` changes)
- A Google Cloud OAuth client (for Sign in with Google)
- API keys for whichever AI providers you enable in `config/ai_models.json` (Groq and/or Gemini)
- Credentials for an HTTP-based transactional email provider
- Local disk (dev) or an S3-compatible bucket (production) for object storage

### 1. Clone and install

```bash
git clone <repository-url>
cd ExamPortal
python -m venv venv

# Windows
venv\Scripts\activate
# Linux / macOS
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

Create a `.env` file in the project root — see [Environment Variables](#environment-variables) below.

### 3. Set up the database

Apply the SQL files in `migrations/` (in filename/date order) against your PostgreSQL database, then point `DATABASE_URL` at it.

### 4. Run locally

```bash
python main.py
```

The app is served at `http://localhost:5000`.

---

## Environment Variables

All configuration is read once, centrally, in `app/config.py`. Never commit `.env` or any credential file.

```env
# Core
SECRET_KEY=change-me
BASE_URL=http://127.0.0.1:5000
APP_TIMEZONE=Asia/Kolkata

# Database
DATABASE_URL=postgresql://user:password@host:5432/dbname
DB_POOL_MIN=1
DB_POOL_MAX=10

# Google OAuth (Sign in with Google)
GOOGLE_OAUTH_CLIENT_ID=xxxx.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
OAUTHLIB_INSECURE_TRANSPORT=1        # 1 for local HTTP dev, 0 in production (HTTPS only)

# Object storage
STORAGE_BACKEND=local                # local | s3
STORAGE_LOCAL_ROOT=./storage
STORAGE_LOCAL_URL_PREFIX=/notes/asset-file
STORAGE_BUCKET=                      # s3 only
STORAGE_ENDPOINT_URL=                # s3 only — any S3-compatible endpoint
STORAGE_REGION=                      # s3 only
STORAGE_ACCESS_KEY=                  # s3 only
STORAGE_SECRET_KEY=                  # s3 only

# Email (generic HTTP provider)
EMAIL_SERVICE_API_KEY=
EMAIL_SERVICE_URL=
DEFAULT_FROM_EMAIL=noreply@your-domain.com
EMAIL_SERVICE_AUTH_HEADER=Authorization
EMAIL_SERVICE_AUTH_PREFIX=Bearer 

# AI — selects which config/ai_models.json entry is active per flow
ASSISTANT_TEXT_MODEL=assistant-default
EXPLANATION_TEXT_MODEL=explanation-default
EXPLANATION_VISION_MODEL_NAME=explanation-vision-default
QUESTION_GENERATOR_TEXT_MODEL=question-generator-default
QUESTION_GENERATOR_VISION_MODEL=question-generator-vision-default

# AI — one API key per registry entry (names must match config/ai_models.json's api_key_env)
TEXT_MODEL_ASSISTANT_API_KEY=
TEXT_MODEL_EXPLANATION_API_KEY=
TEXT_MODEL_QUESTIONGEN_API_KEY=
VISION_MODEL_EXPLANATION_API_KEY=
VISION_MODEL_QUESTIONGEN_API_KEY=

AI_DAILY_LIMIT_PER_STUDENT=50
AI_MAX_MESSAGE_LENGTH=500

# OTP — second-device login verification
OTP_LENGTH=6
OTP_EXPIRY_SECONDS=600
```

### Reference

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask session signing key |
| `DATABASE_URL` | PostgreSQL connection string — any provider |
| `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` | Sign in with Google |
| `STORAGE_BACKEND` | `local` or `s3` — selects the active storage provider |
| `EMAIL_SERVICE_*` | Endpoint, key, and payload shape for the HTTP email provider in use |
| `ASSISTANT_TEXT_MODEL` / `EXPLANATION_*` / `QUESTION_GENERATOR_*` | Which `config/ai_models.json` entry each AI flow uses |
| `*_API_KEY` (per model) | API key for that specific registry entry — see `api_key_env` in `config/ai_models.json` |
| `OTP_*` | Tuning for the existing-active-session email verification flow |
| `APP_TIMEZONE` | Timezone used for all display timestamps (storage is always UTC) |

Adding or swapping an AI model only requires editing `config/ai_models.json` and setting its `api_key_env` variable — no code changes.

---

## Database

PostgreSQL, accessed through a pooled `psycopg2` connection (`app/db/__init__.py`) — no ORM, no vendor-specific client library, so it works identically against Supabase Postgres, Render Postgres, or a self-hosted instance.

Core table groups:

| Group | Tables |
|---|---|
| **Identity & access** | `users`, `sessions`, `login_attempts`, `otp_challenges`, `password_history`, `pw_tokens`, `jwt_refresh_tokens`, `requests_raised` |
| **Exam content** | `categories`, `subcategories`, `exams`, `subjects`, `questions` |
| **Attempts & results** | `exam_attempts`, `results`, `responses` |
| **AI** | `ai_chat_history`, `ai_conversations`, `ai_usage_tracking`, `ai_explanation_history`, `ai_explanation_usage` |
| **Chat** | `chat_conversations`, `chat_messages`, `chat_members`, `chat_unread`, `chat_visibility`, `chat_connections` |
| **Discussions** | `question_discussions`, `discussion_counts` |
| **Notes** | `notes_notebooks`, `notes_pages`, `notes_objects`, `notes_assets`, `notes_revisions`, `notes_bookmarks`, `notes_likes`, `notes_views`, `notes_downloads`, `notes_reports`, `notes_notebook_metrics`, `notes_notebook_shares` |
| **Misc** | `dashboard_event_seen` |

Schema changes are applied as incremental SQL files in `migrations/`, one file per change, applied directly against the database.

---

## Object Storage

Question/category images and notebook attachments go through a provider-independent storage abstraction (`app/storage/`) — application code only ever talks to this interface, never a vendor SDK directly.

- **`local`** — files live under `STORAGE_LOCAL_ROOT` on disk. Nothing else to configure; good for development.
- **`s3`** — any S3-compatible provider (AWS S3, Cloudflare R2, MinIO, Supabase Storage's S3 interface, etc.) via `boto3` with a configurable `endpoint_url`.

Switching providers is a `.env` change, not a code change.

---

## Running in Production

Deployed on [Render](https://render.com) with Gunicorn's gevent WebSocket worker (required for Flask-SocketIO):

```bash
gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
  --workers 1 --bind 0.0.0.0:$PORT --timeout 120 --keep-alive 5 main:app
```

Use a single worker per instance — Flask-SocketIO with gevent needs a shared message queue (e.g. Redis) to scale across multiple workers/instances, which is not currently configured.

Set `OAUTHLIB_INSECURE_TRANSPORT=0` and `SESSION_COOKIE_SECURE`-backing config appropriately once served over HTTPS.

---

## Security Notes

- Passwords hashed with bcrypt; last-3-password reuse blocked via `password_history`
- Server-side sessions, `HttpOnly` cookies, single active session per account (second login requires an emailed OTP)
- Login attempt rate limiting with temporary lockout
- Fullscreen enforcement and tab-switch/visibility monitoring during exams
- Admin access-request removal is a soft delete — the audit row and its history are kept, only hidden from the list, and a user's role is never changed by deleting a request
- All credential files (`.env`, `service_account.json`, `token.json`, `client_secret_web_local.json`) are gitignored

---

## License

Licensed under the [MIT License](LICENSE).
