# ExamPortal — AI-Powered Online Examination Platform

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1.0-000000?style=for-the-badge&logo=flask&logoColor=white)
![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-3ECF8E?style=for-the-badge&logo=supabase&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-Sessions-DC382D?style=for-the-badge&logo=redis&logoColor=white)
![Google Gemini](https://img.shields.io/badge/Gemini_API-AI_Engine-4285F4?style=for-the-badge&logo=google&logoColor=white)
![Bootstrap](https://img.shields.io/badge/Bootstrap-5.3-7952B3?style=for-the-badge&logo=bootstrap&logoColor=white)

**A production-grade, AI-powered examination platform built for modern educational institutions.**  
Secure exam delivery · AI question generation · Real-time analytics · Intelligent study assistance

---

</div>

## Overview

ExamPortal is a full-stack web application that modernizes the online examination workflow for both administrators and students. It integrates Google Gemini AI for automated question generation from academic documents, enforces secure exam environments via fullscreen monitoring and session control, and provides detailed post-exam analytics for both students and instructors.

The platform uses **Redis** for scalable session management and real-time features, **Supabase (PostgreSQL)** as its primary database, and is deployed on Render using Gunicorn with gevent workers.

---

## Key Features

### AI Capabilities
- **AI Question Generator** — Upload PDF documents and automatically extract concepts and generate MCQ questions using the Google Gemini API
- **Configurable Generation** — Control difficulty, question count, and topic coverage
- **Export & Import** — Export AI-generated question sets to CSV or save them directly into the question bank
- **AI Command Centre** — Dedicated admin interface for managing AI generation workflows
- **AI Study Assistant** — Student-facing chat interface powered by Groq LLM (LLaMA 3.3 70B) for exam preparation, with LaTeX-aware responses and daily usage limits

### Exam Management
- Create and configure exams with custom duration, question count, and marking schemes
- Positive and negative marking support with per-question overrides
- Maximum attempt limits per student
- CSV batch upload for bulk question import
- Image upload support for question attachments (integrated with Google Drive)
- Full LaTeX editor for mathematical and scientific question authoring
- MSQ (multi-select) and Numeric answer type support alongside standard MCQ

### Secure Exam Delivery
- Fullscreen enforcement with violation monitoring
- Tab-switch detection and visibility change tracking
- Server-side session management with exam state persistence (Redis-backed)
- Auto-submit on timer expiry
- Progressive answer sync to server during exam

### Result Control System
Administrators have granular control over result visibility:
- **Instant mode** — Results visible immediately after submission
- **Delayed mode** — Results released after a configurable time window
- **Manual release** — Admin triggers result publication for the entire cohort

### Student Experience
- Clean, distraction-free exam interface
- Question palette with answered / reviewed / visited state tracking
- Detailed post-exam result breakdown with score, grade, and accuracy
- Full response review with correct-answer comparison (after result release)
- Exam history and performance trends
- Per-question discussion threads with replies, pinning, and best-answer marking

### Analytics
- Student-level performance analytics with accuracy tracking
- Exam-level statistics across the entire cohort
- Leaderboard / top-performer views
- Detailed response analysis per question
- Admin analytics dashboard with cross-exam comparisons

### Communication
- Built-in peer-to-peer and group chat system with Socket.IO
- Connection request / accept flow
- Real-time presence indicators and unread message badges
- Message edit, delete, and reply-to support
- Per-conversation visibility control (clear history without deleting)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Client (Browser)                     │
│         HTML / CSS / Bootstrap 5 / JavaScript           │
│         Socket.IO · MathJax · KaTeX · LaTeX Editor      │
└───────────────────────┬─────────────────────────────────┘
                        │ HTTP / WebSocket
┌───────────────────────▼─────────────────────────────────┐
│               Flask Application Server                  │
│                                                         │
│  app/routes/        ── Blueprints (exam, auth, admin…)  │
│  app/db/            ── DB layer per domain              │
│  app/services/      ── Business logic layer             │
│  app/utils/         ── Helpers, LaTeX, cache, sanitize  │
│  app/middleware/    ── Session guard, decorators        │
│  main.py            ── App entry point                  │
└─────┬───────────────────────┬───────────────────────────┘
      │                       │
┌─────▼──────────┐   ┌────────▼──────────────────────────┐
│   Supabase     │   │    Redis (Upstash / Render)       │
│  (PostgreSQL)  │   │                                   │
│                │   │  Flask Sessions (server-side)     │
│  Users         │   │  Socket.IO message queue          │
│  Exams         │   └───────────────────────────────────┘
│  Questions     │
│  Results       │   ┌─────────────────────────────────┐
│  Responses     │   │        External APIs            │
│  Sessions      │   │                                 │
│  Chat          │   │  Google Gemini  ── AI generation│
│  Discussions   │   │  Groq LLM      ── Study chat    │
└────────────────┘   │  Google Drive  ── Image storage │
                     │  SMTP / Mailjet── Email service │
                     └─────────────────────────────────┘
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| **Backend Framework** | Python 3.11, Flask 3.1.0 |
| **Real-time** | Flask-SocketIO 5.3.6, gevent-websocket 0.10.1 |
| **Database** | Supabase (PostgreSQL), supabase-py 2.28.0 |
| **Session Store** | Redis 5.0.1 (Flask-Session) |
| **Auth & Sessions** | Flask-Session 0.8.0, bcrypt 5.0.0 |
| **AI — Question Generation** | Google Gemini API (google-genai 1.14.0) |
| **AI — Study Assistant** | Groq API (LLaMA 3.3 70B) |
| **Image Storage** | Google Drive API v3 |
| **PDF** | ReportLab 4.4.10 (generation), pypdf 4.3.1 (parsing), xhtml2pdf 0.2.16 |
| **Frontend** | HTML5, CSS3, Bootstrap 5.3, JavaScript (ES6+) |
| **Math Rendering** | MathJax 3, KaTeX, latex2mathml 3.76.0 |
| **Data Processing** | pandas 2.2.3, numpy 1.26.4 |
| **Email** | SMTP (Gmail) + Mailjet REST API |
| **Deployment** | Render (Gunicorn + gevent-websocket worker) |

---

> [!NOTE]
> # Google Drive API Setup (Service Account + OAuth Token)
> 
> This guide explains how to set up Google Drive API authentication using both:
> - Service Account (for backend/server use)
> - OAuth Token (for user-based uploads)
>
> ---
>
> ## 1. Enable Google Drive API
>
> 1. Go to Google Cloud Console  
>    https://console.cloud.google.com/
>
> 2. Select or create a project
>
> 3. Navigate to:  
>    **APIs & Services → Library**
>
> 4. Search for:  
>    **Google Drive API**
>
> 5. Click **Enable**
>
> ---
>
> ## 2. Create Service Account (service_account.json)
>
> 1. Go to:  
>    **APIs & Services → Credentials**
>
> 2. Click:  
>    **Create Credentials → Service Account**
>
> 3. Enter:
>    - Name: any (e.g., drive-service-account)
>    - Click **Create and Continue**
>
> 4. Skip optional steps → Click **Done**
>
> 5. Open the created service account
>
> 6. Go to:  
>    **Keys → Add Key → Create new key**
>
> 7. Select:  
>    **JSON → Download**
>
> 8. Rename file to:  
>    `service_account.json`
>
> ---
>
> ### Important (Service Account Access)
>
> - Open Google Drive
> - Create or select a folder
> - Share that folder with service account email  
>   (e.g., `xyz@project-id.iam.gserviceaccount.com`)
>
> ---
>
> ## 3. Create OAuth Client (for token.json)
>
> 1. Go to:  
>    **APIs & Services → Credentials**
>
> 2. Click:  
>    **Create Credentials → OAuth Client ID**
>
> 3. If prompted, configure OAuth consent screen:
>    - App name: anything
>    - User type: External
>    - Add your email under **Test Users**
>
> 4. Choose application type:  
>    **Desktop App**
>
> 5. Click **Create**
>
> 6. Download JSON file
>
> 7. Rename it to:  
>    `client_secret_web_local.json`
>
> ---
>
> ## 4. Install Required Library
>
> ```bash
> pip install google-auth-oauthlib
> ```
>
> ---
>
> ## 5. Generate token.json
>
> Create a Python file:
>
> ### `generate_token.py`
>
> ```python
> from google_auth_oauthlib.flow import InstalledAppFlow
> import os
>
> SCOPES = ['https://www.googleapis.com/auth/drive.file']
>
> creds = None
>
> if os.path.exists('token.json'):
>     print("Token already exists")
> else:
>     flow = InstalledAppFlow.from_client_secrets_file(
>         'client_secret_web_local.json', SCOPES)
>     creds = flow.run_local_server(port=0)
>
>     with open('token.json', 'w') as token:
>         token.write(creds.to_json())
>
>     print("token.json generated successfully")
> ```
>
> ---
>
> ## 6. Run Script
>
> ```bash
> python generate_token.py
> ```
>
> ---
>
> ## 7. What Happens
>
> - Browser will open automatically
> - Login with your Google account
> - Grant permissions
> - `token.json` will be generated
>
> ---
>
> ## 8. Final Project Structure
>
> ```
> project/
> ├── service_account.json
> ├── client_secret_web_local.json
> ├── token.json
> └── generate_token.py
> ```
>
> ---
>
> ## 9. Notes
>
> - `token.json` is reusable
> - Do NOT commit credentials to public repositories
> - If you get `access_denied`, add your email in Test Users
>
> ---
>
> ## 10. When to Use What
>
> | Use Case            | Method           |
> |--------------------|------------------|
> | Backend automation | Service Account  |
> | User uploads       | OAuth token.json |

## Installation

### Prerequisites

- Python 3.11
- A [Supabase](https://supabase.com) project with the schema applied
- A Redis instance (local, [Upstash](https://upstash.com), or Render Redis)
- Google Cloud project with **Gemini API** and **Drive API** enabled
- A [Groq](https://console.groq.com) API key
- SMTP / Mailjet credentials for transactional email

### 1. Clone the repository

```bash
git clone https://github.com/your-username/examportal.git
cd examportal
```

### 2. Create and activate a virtual environment

```bash
python -m venv venv

# Linux / macOS
source venv/bin/activate

# Windows
venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** `gunicorn` is commented out in `requirements.txt` for local development. It is installed automatically on Render via the build command. Do not uncomment it locally unless needed.

### 4. Configure environment variables

```bash
cp .env.example .env
# Fill in all values — see Environment Variables section below
```

### 5. Apply the database schema

Run the contents of `supabase_schema.txt` in your Supabase SQL editor to create all required tables.

### 6. Run locally

```bash
python main.py
```

The application will be available at `http://localhost:5000`.

---

## Environment Variables

Create a `.env` file in the project root. **Never commit this file or any credential JSON files to version control.**

```env
# ── Application ───────────────────────────────────────────────────────────────
SECRET_KEY=your-secret-key-here
BASE_URL=http://127.0.0.1:5000

# ── Supabase ───────────────────────────────────────────────────────────────────
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=your-supabase-anon-or-service-role-key

# ── Redis ─────────────────────────────────────────────────────────────────────
REDIS_URL=redis://default:password@host:port

# ── Google OAuth & Service Account ────────────────────────────────────────────
GOOGLE_SERVICE_ACCOUNT_JSON=service_account.json
GOOGLE_SERVICE_TOKEN_JSON=token.json
GOOGLE_OAUTH_CLIENT_JSON=client_secret_web_local.json
OAUTHLIB_INSECURE_TRANSPORT=1        # Set to 0 in production (HTTPS only)

# ── Google Drive ───────────────────────────────────────────────────────────────
ROOT_FOLDER_ID=your-root-drive-folder-id
IMAGES_FOLDER_ID=your-images-drive-folder-id

# ── Email ─────────────────────────────────────────────────────────────────────
EMAIL_ADDRESS=your-gmail@gmail.com
FROM_EMAIL=your-gmail@gmail.com
MAILJET_API_KEY=your-mailjet-api-key
MAILJET_API_SECRET=your-mailjet-api-secret

# ── AI — Groq (Study Assistant) ───────────────────────────────────────────────
GROQ_API_KEY=gsk_xxxx
AI_MODEL_NAME=llama-3.3-70b-versatile
AI_DAILY_LIMIT_PER_STUDENT=50
AI_MAX_MESSAGE_LENGTH=500
AI_REQUEST_TIMEOUT=30

# ── AI — Gemini (Question Generation) ────────────────────────────────────────
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL_NAME=gemini-2.5-flash
```

### Environment Variable Reference

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | ✅ | Flask session signing key — use a long random string |
| `BASE_URL` | ✅ | Full base URL of the app (used in email links) |
| `SUPABASE_URL` | ✅ | Supabase project URL |
| `SUPABASE_KEY` | ✅ | Supabase anon or service role key |
| `REDIS_URL` | ✅ | Redis connection URL (used for sessions and Socket.IO) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | ✅ | Path to Google service account credentials JSON |
| `GOOGLE_SERVICE_TOKEN_JSON` | ✅ | Path to cached OAuth token JSON |
| `GOOGLE_OAUTH_CLIENT_JSON` | ✅ | Path to OAuth client secret JSON |
| `OAUTHLIB_INSECURE_TRANSPORT` | ✅ | `1` for local HTTP dev, `0` for HTTPS production |
| `ROOT_FOLDER_ID` | ✅ | Google Drive root folder ID |
| `IMAGES_FOLDER_ID` | ✅ | Google Drive folder for question images |
| `EMAIL_ADDRESS` | ✅ | Gmail address used for SMTP sending |
| `FROM_EMAIL` | ✅ | Sender address shown in outgoing emails |
| `MAILJET_API_KEY` | ✅ | Mailjet API key |
| `MAILJET_API_SECRET` | ✅ | Mailjet API secret |
| `GROQ_API_KEY` | ✅ | Groq API key for the AI study assistant |
| `AI_MODEL_NAME` | ✅ | Groq model (default: `llama-3.3-70b-versatile`) |
| `AI_DAILY_LIMIT_PER_STUDENT` | ⚙️ | Max AI messages per student per day (default: `50`) |
| `AI_MAX_MESSAGE_LENGTH` | ⚙️ | Max characters per AI message (default: `500`) |
| `AI_REQUEST_TIMEOUT` | ⚙️ | Groq request timeout in seconds (default: `30`) |
| `GEMINI_API_KEY` | ✅ | Google Gemini API key for question generation |
| `GEMINI_MODEL_NAME` | ✅ | Gemini model (default: `gemini-2.5-flash`) |

> ⚠️ **Never commit** `.env`, `service_account.json`, `token.json`, or `client_secret_web_local.json`. All four must be in `.gitignore`.

---

## Running in Production

The application is deployed on [Render](https://render.com) using Gunicorn with the `GeventWebSocketWorker` for WebSocket support.

**Render start command:**

```bash
gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --workers 1 --bind 0.0.0.0:$PORT --timeout 120 --keep-alive 5 main:app
```

> **Note:** Use a single worker (`--workers 1`) for Flask-SocketIO with gevent in single-instance deployments. For multi-instance horizontal scaling, configure a Redis message queue and set `SESSION_TYPE=redis` (already the case when `REDIS_URL` is set).

### Environment-specific settings

| Variable | Development | Production |
|---|---|---|
| `OAUTHLIB_INSECURE_TRANSPORT` | `1` | `0` |
| `BASE_URL` | `http://127.0.0.1:5000` | `https://your-app.onrender.com` |
| `FLASK_DEBUG` | `True` | `False` |

---

## Project Structure

```
examportal/
│
├── main.py                          # App entry point, app factory, Socket.IO init
├── config.py                        # Config class (loads .env)
├── supabase_db.py                   # Legacy DB abstraction (being phased out)
├── ai_question_generator.py         # Google Gemini AI integration
├── google_drive_service.py          # Google Drive service initialisation
├── email_utils.py                   # Transactional email (password reset, setup)
├── login_attempts_cache.py          # In-memory login attempt rate limiting
├── sessions.py                      # Session utility helpers
├── chat.py                          # Real-time chat Socket.IO handlers
├── discussion.py                    # Question discussion Socket.IO handlers
├── latex_editor.py                  # LaTeX editor blueprint
├── requirements.txt                 # Python dependencies (gunicorn commented for local)
├── supabase_schema.txt              # DB schema reference — run once on Supabase
│
├── app/
│   ├── __init__.py                  # Blueprint registration
│   │
│   ├── db/                          # Database access layer (per-domain modules)
│   │   ├── ai.py                    # AI usage tracking queries
│   │   ├── attempts.py              # Exam attempt queries
│   │   ├── auth.py                  # Auth/user lookup queries
│   │   ├── exams.py                 # Exam CRUD queries
│   │   ├── misc.py                  # Utility queries
│   │   ├── questions.py             # Question bank queries
│   │   ├── results.py               # Result & response queries
│   │   ├── sessions.py              # Session table queries
│   │   └── users.py                 # User management queries
│   │
│   ├── middleware/
│   │   └── session_guard.py         # Auth decorators (login_required, admin_required, etc.)
│   │
│   ├── routes/
│   │   ├── ai_assistant.py          # AI study assistant routes
│   │   ├── auth.py                  # Login, logout, registration
│   │   ├── dashboard.py             # Student dashboard
│   │   ├── exam.py                  # Exam delivery (start, submit, sync)
│   │   ├── misc.py                  # Static pages, error handlers
│   │   ├── result.py                # Result view, response review
│   │   │
│   │   └── admin/                   # Admin-only blueprints
│   │       ├── ai_centre.py         # AI Command Centre
│   │       ├── attempts.py          # Student attempt overview
│   │       ├── auth.py              # Admin login/logout
│   │       ├── dashboard.py         # Admin dashboard
│   │       ├── exams.py             # Exam management
│   │       ├── images.py            # Question image upload
│   │       ├── questions.py         # Question bank management
│   │       ├── requests.py          # Admin access request handling
│   │       ├── results.py           # Results & release control
│   │       ├── subjects.py          # Subject management
│   │       └── users.py             # User management & analytics
│   │
│   ├── services/                    # Business logic layer
│   │   ├── ai_service.py            # Groq / LLM chat orchestration
│   │   ├── auth_service.py          # Authentication logic
│   │   ├── drive_service.py         # Google Drive operations
│   │   ├── email_service.py         # Email dispatch logic
│   │   ├── exam_service.py          # Exam session & scoring logic
│   │   ├── pdf_service.py           # PDF generation
│   │   └── result_service.py        # Result calculation & grading
│   │   └── user_deletion_service.py # Delete user account
│   │
│   └── utils/                       # Shared utilities
│       ├── cache.py                 # Redis / in-memory cache helpers
│       ├── helpers.py               # General-purpose helpers
│       ├── latex.py                 # LaTeX processing utilities
│       └── sanitize.py             # Input sanitization
│
├── templates/
│   ├── base.html                    # Base layout (navbar, footer, dark theme)
│   ├── index.html                   # Landing / home page
│   ├── login.html                   # User authentication
│   ├── create_account.html          # Student self-registration
│   ├── dashboard.html               # Student dashboard
│   ├── exam_instructions.html       # Pre-exam instructions
│   ├── exam_page.html               # Secure exam delivery interface
│   ├── result.html                  # Post-exam result summary
│   ├── result_pending.html          # Result pending (awaiting admin release)
│   ├── response.html                # Detailed response review
│   ├── results_history.html         # Exam history list
│   ├── student_analytics.html       # Student performance analytics
│   ├── ai_assistant.html            # AI study assistant chat
│   ├── chat.html                    # Peer-to-peer / group chat
│   ├── select_portal.html           # Portal selector (multi-role accounts)
│   ├── password_reset.html          # Password reset request
│   ├── password_reset_form.html     # Token-based password reset
│   ├── password_setup_form.html     # Account creation password setup
│   ├── registration_success.html    # Post-registration confirmation
│   ├── logout_redirect.html         # Post-logout redirect
│   ├── request_admin_access.html    # Admin access request form
│   ├── error.html                   # Generic error page
│   ├── about.html / contact.html / support.html
│   ├── privacy_policy.html / terms_of_service.html
│   │
│   └── admin/
│       ├── admin_base.html          # Admin layout base
│       ├── admin_login.html         # Admin authentication
│       ├── dashboard.html           # Admin dashboard
│       ├── ai_command_centre.html   # AI question generation hub
│       ├── exams.html               # Exam management
│       ├── questions.html           # Question bank
│       ├── csv_upload.html          # Bulk CSV question import (modal partial)
│       ├── upload_images.html       # Question image upload
│       ├── attempts.html            # Student attempt overview
│       ├── users_manage.html        # User management
│       ├── users_analytics.html     # Cross-student analytics
│       ├── subjects.html            # Subject management
│       ├── requests.html            # Pending admin access requests
│       ├── requests_history.html    # Access request history
│       ├── new_requests.html        # New request notifications
│       ├── latex_editor.html        # In-browser LaTeX editor
│       └── view_responses_popup.html / view_result_popup.html
│
└── static/
    └── favicon.png
```

---

## Python Dependencies

All dependencies are pinned in `requirements.txt`. Key packages:

| Package | Version | Purpose |
|---|---|---|
| `flask` | 3.1.0 | Core web framework |
| `flask-socketio` | 5.3.6 | WebSocket / real-time support |
| `flask-session` | 0.8.0 | Server-side session management |
| `gevent-websocket` | 0.10.1 | gevent WebSocket worker for Gunicorn |
| `redis` | 5.0.1 | Redis client (sessions + Socket.IO queue) |
| `supabase` | 2.28.0 | Supabase Python client |
| `google-genai` | 1.14.0 | Google Gemini AI (question generation) |
| `google-api-python-client` | 2.191.0 | Google Drive API |
| `google-auth` | 2.34.0 | Google OAuth2 |
| `google-auth-oauthlib` | 1.3.0 | Google OAuth flow |
| `bcrypt` | 5.0.0 | Password hashing |
| `reportlab` | 4.4.10 | PDF generation |
| `pypdf` | 4.3.1 | PDF parsing |
| `xhtml2pdf` | 0.2.16 | HTML-to-PDF conversion |
| `latex2mathml` | 3.76.0 | LaTeX to MathML conversion |
| `pandas` | 2.2.3 | CSV processing |
| `numpy` | 1.26.4 | Numerical computing |
| `orjson` | 3.11.7 | Fast JSON serialization |
| `python-dotenv` | 1.0.1 | Environment variable loading |
| `mailjet-rest` | 1.3.4 | Mailjet email API client |
| `requests` | 2.31.0 | HTTP client |
| `aiohttp` | 3.10.5 | Async HTTP |
| `gunicorn` | *(prod only)* | WSGI server — **commented out locally** |

> `gunicorn` is commented out in `requirements.txt` for local development to avoid install issues. Render installs it automatically via the build process.

---

## Database Schema

The platform uses PostgreSQL via Supabase. Core tables:

| Table | Purpose |
|---|---|
| `users` | Student and admin accounts |
| `exams` | Exam configuration and metadata |
| `questions` | Question bank (MCQ, MSQ, Numeric) |
| `exam_attempts` | Attempt tracking per student per exam |
| `results` | Aggregated result records |
| `responses` | Per-question student responses |
| `sessions` | Server-side session store |
| `subjects` | Subject / folder management |
| `ai_chat_history` | AI study assistant conversation logs |
| `ai_usage_tracking` | Daily AI usage limits per student |
| `chat_conversations` | Chat threads (direct + group) |
| `chat_messages` | Individual chat messages (with edit/reply) |
| `chat_members` | Conversation membership |
| `chat_unread` | Unread message counts per user |
| `chat_visibility` | Per-user conversation clear timestamps |
| `chat_connections` | Connection request / accept flow |
| `question_discussions` | Per-question discussion threads |
| `discussion_counts` | Cached discussion count per question |
| `login_attempts` | Failed login tracking and lockout |
| `requests_raised` | Admin access request workflow |
| `pw_tokens` | Password reset / setup tokens |

The full schema with all constraints is in `supabase_schema.txt`. Run it once in your Supabase SQL editor to initialise the database.

---

-- =====================================================
-- FULL DATABASE CLONE (TABLES + PK + FK + INDEXES)
-- RLS / FUNCTIONS IGNORED AS REQUESTED
-- =====================================================

-- =========================
-- SEQUENCES
-- =========================
CREATE SEQUENCE users_id_seq;
CREATE SEQUENCE subjects_id_seq;
CREATE SEQUENCE questions_id_seq;
CREATE SEQUENCE results_id_seq;
CREATE SEQUENCE ai_chat_history_id_seq;
CREATE SEQUENCE login_attempts_id_seq;
CREATE SEQUENCE ai_usage_tracking_id_seq;
CREATE SEQUENCE categories_id_seq;
CREATE SEQUENCE chat_connections_id_seq;
CREATE SEQUENCE chat_conversations_id_seq;
CREATE SEQUENCE chat_members_id_seq;
CREATE SEQUENCE chat_messages_id_seq;
CREATE SEQUENCE chat_unread_id_seq;
CREATE SEQUENCE exam_attempts_id_seq;
CREATE SEQUENCE exams_id_seq;
CREATE SEQUENCE pw_tokens_id_seq;
CREATE SEQUENCE question_discussions_id_seq;
CREATE SEQUENCE requests_raised_request_id_seq;
CREATE SEQUENCE responses_id_seq;
CREATE SEQUENCE sessions_id_seq;

-- =========================
-- CORE TABLES
-- =========================
CREATE TABLE users (
  id integer PRIMARY KEY DEFAULT nextval('users_id_seq'),
  username varchar NOT NULL UNIQUE,
  email varchar NOT NULL UNIQUE,
  password varchar NOT NULL,
  full_name varchar,
  created_at timestamp DEFAULT now(),
  role varchar DEFAULT 'user',
  updated_at timestamp,
  last_login timestamp,
  username_lower varchar,
  email_lower varchar
);

CREATE TABLE categories (
  id integer PRIMARY KEY DEFAULT nextval('categories_id_seq'),
  name varchar NOT NULL UNIQUE,
  drive_file_id varchar,
  image_url varchar,
  created_at timestamp DEFAULT now()
);

CREATE TABLE subjects (
  id integer PRIMARY KEY DEFAULT nextval('subjects_id_seq'),
  subject_name varchar NOT NULL,
  subject_folder_id varchar,
  subject_folder_created_at timestamp
);

CREATE TABLE login_attempts (
  id integer PRIMARY KEY DEFAULT nextval('login_attempts_id_seq'),
  identifier varchar NOT NULL,
  ip_address varchar NOT NULL,
  failed_count integer DEFAULT 0,
  first_failed_at timestamp DEFAULT now(),
  last_failed_at timestamp DEFAULT now(),
  blocked_until timestamp
);

CREATE TABLE pw_tokens (
  id integer PRIMARY KEY DEFAULT nextval('pw_tokens_id_seq'),
  token varchar NOT NULL UNIQUE,
  email varchar NOT NULL,
  expires_at timestamp NOT NULL,
  used boolean DEFAULT false,
  created_at timestamp DEFAULT now(),
  type varchar
);

-- =========================
-- EXAM SYSTEM
-- =========================
CREATE TABLE exams (
  id integer PRIMARY KEY DEFAULT nextval('exams_id_seq'),
  name varchar NOT NULL,
  date varchar,
  start_time varchar,
  duration integer DEFAULT 60,
  total_questions integer DEFAULT 0,
  status varchar DEFAULT 'draft',
  instructions text,
  positive_marks varchar,
  negative_marks varchar,
  max_attempts integer,
  result_mode varchar DEFAULT 'instant',
  result_delay integer DEFAULT 0,
  results_released boolean DEFAULT false,
  category_id integer REFERENCES categories(id)
);

CREATE TABLE questions (
  id integer PRIMARY KEY DEFAULT nextval('questions_id_seq'),
  exam_id integer REFERENCES exams(id),
  question_text text NOT NULL,
  option_a text,
  option_b text,
  option_c text,
  option_d text,
  correct_answer text,
  question_type varchar DEFAULT 'MCQ',
  image_path text,
  positive_marks integer DEFAULT 1,
  negative_marks numeric DEFAULT 0,
  tolerance numeric DEFAULT 0
);

CREATE TABLE results (
  id integer PRIMARY KEY DEFAULT nextval('results_id_seq'),
  student_id integer REFERENCES users(id),
  exam_id integer,
  score integer DEFAULT 0,
  total_questions integer DEFAULT 0,
  correct_answers integer DEFAULT 0,
  incorrect_answers integer DEFAULT 0,
  unanswered_questions integer DEFAULT 0,
  max_score integer DEFAULT 0,
  percentage numeric DEFAULT 0,
  grade varchar,
  time_taken_minutes integer DEFAULT 0,
  completed_at timestamp DEFAULT now()
);

CREATE TABLE responses (
  id integer PRIMARY KEY DEFAULT nextval('responses_id_seq'),
  result_id integer REFERENCES results(id),
  exam_id integer,
  question_id integer REFERENCES questions(id),
  given_answer text,
  correct_answer text,
  is_correct boolean DEFAULT false,
  marks_obtained numeric DEFAULT 0,
  question_type varchar,
  is_attempted boolean DEFAULT false
);

CREATE TABLE exam_attempts (
  id integer PRIMARY KEY DEFAULT nextval('exam_attempts_id_seq'),
  student_id integer REFERENCES users(id),
  exam_id integer,
  attempt_number integer DEFAULT 1,
  status varchar DEFAULT 'in_progress',
  start_time timestamp,
  end_time timestamp
);

-- =========================
-- DISCUSSION SYSTEM
-- =========================
CREATE TABLE discussion_counts (
  question_id integer PRIMARY KEY REFERENCES questions(id),
  count integer DEFAULT 0
);

CREATE TABLE question_discussions (
  id integer PRIMARY KEY DEFAULT nextval('question_discussions_id_seq'),
  question_id integer REFERENCES questions(id),
  exam_id integer REFERENCES exams(id),
  user_id integer REFERENCES users(id),
  username varchar NOT NULL,
  message text NOT NULL,
  parent_id integer,
  is_pinned boolean DEFAULT false,
  is_best_answer boolean DEFAULT false,
  is_deleted boolean DEFAULT false,
  is_edited boolean DEFAULT false,
  created_at timestamp DEFAULT now(),
  updated_at timestamp DEFAULT now()
);

-- =========================
-- AI SYSTEM
-- =========================
CREATE TABLE ai_chat_history (
  id integer PRIMARY KEY DEFAULT nextval('ai_chat_history_id_seq'),
  user_id integer REFERENCES users(id),
  message text NOT NULL,
  is_user boolean DEFAULT true,
  timestamp timestamp DEFAULT now()
);

CREATE TABLE ai_usage_tracking (
  id integer PRIMARY KEY DEFAULT nextval('ai_usage_tracking_id_seq'),
  user_id integer REFERENCES users(id),
  date date DEFAULT CURRENT_DATE,
  questions_used integer DEFAULT 0
);

-- =========================
-- SESSION MANAGEMENT
-- =========================
CREATE TABLE sessions (
  id integer PRIMARY KEY DEFAULT nextval('sessions_id_seq'),
  token varchar NOT NULL UNIQUE,
  user_id integer REFERENCES users(id),
  device_info text,
  last_seen timestamp DEFAULT now(),
  is_exam_active boolean DEFAULT false,
  exam_id integer,
  result_id integer,
  admin_session boolean DEFAULT false,
  active boolean DEFAULT true,
  created_at timestamp DEFAULT now()
);

-- =========================
-- CHAT SYSTEM
-- =========================
CREATE TABLE chat_conversations (
  id integer PRIMARY KEY DEFAULT nextval('chat_conversations_id_seq'),
  is_group boolean DEFAULT false,
  group_name varchar,
  created_by integer REFERENCES users(id),
  created_at timestamp DEFAULT now()
);

CREATE TABLE chat_connections (
  id integer PRIMARY KEY DEFAULT nextval('chat_connections_id_seq'),
  requester_id integer REFERENCES users(id),
  recipient_id integer REFERENCES users(id),
  status varchar DEFAULT 'pending',
  created_at timestamp DEFAULT now(),
  updated_at timestamp DEFAULT now()
);

CREATE TABLE chat_members (
  id integer PRIMARY KEY DEFAULT nextval('chat_members_id_seq'),
  conversation_id integer REFERENCES chat_conversations(id),
  user_id integer REFERENCES users(id),
  joined_at timestamp DEFAULT now(),
  role varchar DEFAULT 'member'
);

CREATE TABLE chat_messages (
  id integer PRIMARY KEY DEFAULT nextval('chat_messages_id_seq'),
  conversation_id integer REFERENCES chat_conversations(id),
  sender_id integer REFERENCES users(id),
  sender_name varchar NOT NULL,
  message text NOT NULL,
  is_deleted boolean DEFAULT false,
  created_at timestamp DEFAULT now(),
  is_edited boolean DEFAULT false,
  reply_to_id integer,
  reply_to_text varchar,
  reply_to_name varchar
);

CREATE TABLE chat_unread (
  id integer PRIMARY KEY DEFAULT nextval('chat_unread_id_seq'),
  user_id integer REFERENCES users(id),
  conversation_id integer REFERENCES chat_conversations(id),
  count integer DEFAULT 0
);

CREATE TABLE chat_visibility (
  id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id integer REFERENCES users(id),
  conversation_id integer REFERENCES chat_conversations(id),
  cleared_at timestamp DEFAULT now()
);

-- =========================
-- MISC
-- =========================
CREATE TABLE requests_raised (
  request_id integer PRIMARY KEY DEFAULT nextval('requests_raised_request_id_seq'),
  username varchar,
  email varchar,
  current_access varchar,
  requested_access varchar,
  request_date timestamp DEFAULT now(),
  request_status varchar DEFAULT 'pending',
  reason text,
  processed_by varchar,
  processed_date timestamp
);

-- =========================
-- INDEXES (PERFORMANCE)
-- =========================

CREATE INDEX idx_users_username_lower ON users (username_lower);
CREATE INDEX idx_users_email_lower ON users (email_lower);
CREATE INDEX idx_exams_category_id ON exams (category_id);
CREATE INDEX idx_questions_exam_id ON questions (exam_id);
CREATE INDEX idx_results_student_id ON results (student_id);
CREATE INDEX idx_results_exam_id ON results (exam_id);
CREATE INDEX idx_responses_result_id ON responses (result_id);
CREATE INDEX idx_responses_question_id ON responses (question_id);
CREATE INDEX idx_qd_question_id ON question_discussions (question_id);
CREATE INDEX idx_qd_user_id ON question_discussions (user_id);
CREATE INDEX idx_ai_chat_user_id ON ai_chat_history (user_id);
CREATE INDEX idx_ai_usage_user_date ON ai_usage_tracking (user_id, date);
CREATE INDEX idx_sessions_user_id ON sessions (user_id);
CREATE INDEX idx_chat_conv_created_by ON chat_conversations (created_by);
CREATE INDEX idx_chat_conn_requester ON chat_connections (requester_id);
CREATE INDEX idx_chat_conn_recipient ON chat_connections (recipient_id);
CREATE INDEX idx_chat_members_conv ON chat_members (conversation_id);
CREATE INDEX idx_chat_members_user ON chat_members (user_id);
CREATE INDEX idx_chat_messages_conv ON chat_messages (conversation_id);
CREATE INDEX idx_chat_messages_sender ON chat_messages (sender_id);
CREATE INDEX idx_chat_unread_user_conv ON chat_unread (user_id, conversation_id);
CREATE INDEX idx_login_identifier_ip ON login_attempts (identifier, ip_address);

-- =====================================================
-- END OF FULL DATABASE ARCHITECTURE
-- =====================================================

## Security

- Passwords hashed with **bcrypt** (no plaintext storage anywhere)
- Server-side sessions via **Redis** with `HttpOnly` and `Secure` cookie flags
- Login attempt rate limiting with automatic temporary lockout (`login_attempts` table)
- Exam sessions are isolated and validated server-side on every request
- Fullscreen enforcement deters screen-sharing during exams
- Tab-switch and visibility-change monitoring with configurable violation thresholds
- Progressive answer sync prevents data loss on connection drops
- All credential files (`.env`, `service_account.json`, `token.json`, `client_secret_web_local.json`) are gitignored

---

## Future Improvements

- **Proctoring integration** — webcam snapshot analysis during exams
- **Question bank tagging** — difficulty levels, topic tags, Bloom's taxonomy
- **Adaptive exam engine** — dynamic question selection based on performance
- **Detailed item analysis** — discrimination index and difficulty coefficient per question
- **LMS integration** — LTI 1.3 provider support (Moodle, Canvas, Blackboard)
- **Multi-language support** — i18n for question content and UI
- **Admin mobile app** — React Native companion for on-the-go management

---

## License

This project is licensed under the [MIT License](LICENSE).

---

<div align="center">

Built with Flask · Powered by Gemini AI · Backed by Supabase · Sessions on Redis

</div>
