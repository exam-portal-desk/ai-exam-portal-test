<div align="center">

<img src="static/favicon.png" alt="Penny Wise" width="72" height="72">

# SmartAIExam

An online examination platform built with Flask and PostgreSQL. Administrators organise exams and question banks and control when results are released. Students sit exams in a timed interface, review their responses, and use an AI study assistant. Every AI call, the plans students are on, and the storage backend are driven by configuration rather than hard-coded.

<div align="center">
## Features

**Students**
- Timed MCQ, MSQ and numeric exams, with manual or scheduled start windows, attempt limits and automatic submission at the deadline
- Results with ranking, full response review (including question images) and personal analytics
- **AI Assistant** for study questions, **AI Explanations** of answers, and **Discuss with AI**, which opens the Assistant on one reviewed question with that question and the student's answer attached
- Per-question discussion threads, one-to-one and group chat (Socket.IO), and notebooks with a drawing canvas, sharing and a public library
- Sign-in with email and password or Google; an emailed one-time code is required when a second session is opened

**Administrators**
- Categories, subcategories, subjects, exams and questions, with CSV import, a LaTeX editor, question images and AI-assisted question generation from PDFs and images
- Result visibility per exam: instant, delayed or released manually
- Attempt management: see how many attempts each student has used per exam, then reset, add or remove them
- Dashboard and User Analytics with chart export to PNG (generated in the browser) and per-result PDF download
- Users, access requests and plans, with each admin screen gated by its own named permission
- **AI Configuration**: choose which model powers each AI feature, compare models, and see provider key status (never the key)

**Plans and access.** Free, Pro and Elite, the features each includes and every per-student limit are defined in [`config/entitlements.json`](config/entitlements.json) (guide: [`config/ENTITLEMENTS.md`](config/ENTITLEMENTS.md)). Plans are assigned by an administrator and may have an expiry. The public `/plans` page and the landing-page plans section read the same file.

## Stack

| Area | Technology |
|---|---|
| Backend | Python 3.11, Flask 3.1, Flask-Session, Flask-SocketIO on gevent |
| Database | PostgreSQL through `psycopg2` with a connection pool; no ORM |
| Auth | bcrypt, Authlib (Google OpenID Connect), optional bearer-token middleware |
| AI | Provider-neutral registry in [`config/ai_models.json`](config/ai_models.json); Groq and Gemini are called over HTTP |
| Storage | Local disk or any S3-compatible bucket (`boto3`) |
| Email | Any provider with an HTTP API |
| Frontend | Jinja templates, Bootstrap 5, vanilla JavaScript, Chart.js, MathJax and KaTeX |
| Server | Gunicorn with the gevent-websocket worker |

## Getting started

Requirements: Python 3.11, a PostgreSQL database, and Node.js (only needed by two test suites).

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Configure

The app reads a `.env` file in the project root (loaded with `python-dotenv`). The repository has no template, so create one. These are the values you need for a working local setup:

```env
SECRET_KEY=<long random string>
DATABASE_URL=postgresql://user:password@host:5432/dbname
BASE_URL=http://localhost:5000
FORCE_SECURE_COOKIES=0            # local HTTP only; leave unset (secure cookies) in production

# Email: one-time codes and password reset
EMAIL_SERVICE_URL=
EMAIL_SERVICE_API_KEY=
DEFAULT_FROM_EMAIL=

# AI: a key for each provider you use in config/ai_models.json
GROQ_API_KEY=
GEMINI_API_KEY=
```

Never commit `.env`; it is already in `.gitignore`. Everything else is optional and listed in the [configuration reference](#configuration-reference).

### Database

`migrations/` holds the incremental SQL changes, named by date. Apply them in filename order to a database that already has the base schema; the base schema itself is not part of this repository. Run each file once:

```bash
psql "$DATABASE_URL" -f migrations/<file>.sql
```

### Run

```bash
python main.py
```

The app listens on `http://localhost:5000` (override with `PORT`). Debug mode is on unless the `RENDER` environment variable is set.

### First administrator

Give an existing account the Admin role (in the database), then make it a root administrator:

```bash
python scripts/grant_root.py <username-or-email>
```

A root administrator can grant admin features to other administrators from the portal.

### Tests

```bash
python -m unittest tests.test_admin_attempts tests.test_results_filters tests.test_entitlements tests.test_plan_expiry tests.test_ai_layer
```

Run suites by module name: some suites share global state, so one `unittest discover` run can behave differently from running the modules individually. `test_explanation_ui` and `test_stream_render` need Node.js. The suites above use fakes, mocks and an in-memory SQLite database instead of a live PostgreSQL database.

## Configuration reference

All settings are read in [`app/config.py`](app/config.py).

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | placeholder | Flask session signing. Always set it in production |
| `DATABASE_URL` | none | PostgreSQL connection string |
| `DB_POOL_MIN` / `DB_POOL_MAX` | 1 / 10 | Connection pool size |
| `BASE_URL` | placeholder | Public URL, used in emails |
| `APP_TIMEZONE` | `Asia/Kolkata` | Display timezone; timestamps are stored in UTC |
| `DISPLAY_DATETIME_FORMAT`, `DISPLAY_DATE_FORMAT` | `%d %B %Y %I:%M %p`, `%d %B %Y` | Display formats |
| `FORCE_SECURE_COOKIES` | `1` | Set `0` to allow session cookies over plain HTTP |
| `SESSION_TYPE`, `SESSION_FILE_DIR`, `PERMANENT_SESSION_LIFETIME` | filesystem, temp dir, 10800 s | Server-side sessions |
| `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` | none | Enables Sign in with Google |
| `EMAIL_SERVICE_URL`, `EMAIL_SERVICE_API_KEY`, `DEFAULT_FROM_EMAIL` | none | Transactional email |
| `EMAIL_SERVICE_AUTH_HEADER`, `EMAIL_SERVICE_AUTH_PREFIX`, `EMAIL_SERVICE_PAYLOAD_TEMPLATE` | `Authorization`, `Bearer `, JSON template | Shape of the provider's request |
| `STORAGE_BACKEND` | `local` | `local` or `s3` |
| `STORAGE_LOCAL_ROOT`, `STORAGE_LOCAL_URL_PREFIX` | `./storage`, `/notes/asset-file` | Local backend |
| `STORAGE_BUCKET`, `STORAGE_ENDPOINT_URL`, `STORAGE_REGION`, `STORAGE_ACCESS_KEY`, `STORAGE_SECRET_KEY` | none | S3-compatible backend |
| `GROQ_API_KEY`, `GEMINI_API_KEY` | none | Provider keys named in `config/ai_models.json` |
| `TEXT_MODEL_*_API_KEY`, `VISION_MODEL_*_API_KEY` | none | Optional per-feature keys; a feature uses its own key if set, otherwise its provider's |
| `AI_MODELS_CONFIG_PATH`, `ENTITLEMENTS_CONFIG_PATH` | files in `config/` | Alternative locations for the two JSON configs |
| `AI_CONFIG_CACHE_TTL_SECONDS` | 30 | How long a worker caches the registry and admin model choices |
| `AI_MAX_MESSAGE_LENGTH` | 500 | Assistant message length cap |
| `AI_REQUEST_TIMEOUT`, `AI_CONNECT_TIMEOUT`, `AI_STREAM_TOTAL_TIMEOUT`, `AI_LOG_LEVEL` | see `app/config.py` | AI request behaviour and logging |
| `AUTO_SUBMIT_SWEEP_INTERVAL_SECONDS`, `AUTO_SUBMIT_BATCH_SIZE` | 5, 200 | Deadline auto-submit sweep |
| `OTP_*` | see `app/config.py` | One-time code length, expiry and rate limits |
| `NOTES_TRASH_RETENTION_DAYS`, `NOTES_TRASH_CLEANUP_ENABLED` | 30, `1` | Notebook trash |
| `MAX_IMAGE_SIZE_KB`, `MAX_PROFILE_PHOTO_SIZE_KB`, `MAX_CHAT_BACKGROUND_SIZE_KB` | 500, 200, 500 | Upload limits |
| `PUBLIC_SUPPORT_EMAIL`, `PUBLIC_CONTACT_PHONE`, `PUBLIC_ADDRESS`, `PUBLIC_SOCIAL_*` | blank | Footer and contact details; a blank value hides the element |
| `CEO_NAME`, `CEO_TITLE`, `CEO_IMAGE_KEY`, `LOGO_ASSET_URL` | blank | Landing-page founder section and email logo |
| `HUB_URL`, `HUB_NAME` | blank, `Sarvadwar` | Optional link back to a product hub in the landing header |
| `LEGAL_*_LAST_UPDATED` | blank | "Last updated" lines on the policy pages |

Models are never chosen through environment variables. The default model for each AI feature comes from `config/ai_models.json`, and an administrator can override it per feature in **AI Configuration**. The guide for adding or changing models is [`config/AI_MODELS.md`](config/AI_MODELS.md).

## Deployment

The project is deployed on Render, which sets `RENDER` and `PORT`. Start command:

```bash
gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
  --workers 1 --bind 0.0.0.0:$PORT --timeout 120 main:app
```

Operational notes:
- **One worker.** Socket.IO has no message queue configured, so run a single worker per instance.
- **Sessions are files** by default. On a host with an ephemeral disk, a redeploy signs everyone out.
- **Storage.** Use `STORAGE_BACKEND=s3` in production; local disk does not survive redeploys on most hosts.
- **Background work runs in the app process.** The deadline auto-submit sweep starts with the app, so scheduled exams are finalised only while it is running.
- **Secrets** (`SECRET_KEY`, database, email, storage and AI keys) belong in the host's environment settings.
- **Configuration files.** Changes to `config/ai_models.json` are picked up within `AI_CONFIG_CACHE_TTL_SECONDS`, or immediately with **Reload configuration** in the admin page. `config/entitlements.json` is re-read when the file changes.

## Project structure

The complete file listing is in [`project_structure.txt`](project_structure.txt).

```
main.py                 entry point (gevent patch, app factory, socketio.run)
config/                 ai_models.json, entitlements.json and their guides
migrations/             incremental SQL migrations
scripts/                grant_root.py
tests/                  unittest suites and two Node-based render checks (tests/js)
app/
  config.py             every environment variable, read in one place
  entitlements/         plans, access resolution, expiry policy, admin authority
  routes/web/           server-rendered pages (admin/ for admin pages)
  routes/api/v01/       JSON API (admin/ for admin endpoints)
  services/             exam scoring, AI (services/ai: registry and provider adapters), email, PDF, notes, chat
  db/                   one module per table group; all SQL lives here
  storage/              local and S3 backends
  middleware/           session guard, bearer-token middleware
  utils/                datetime, LaTeX, cache, sanitising, pagination
templates/              Jinja templates (admin/, notes/, emails/, partials/)
static/                 theme.css, shared and admin UI scripts, notebook editor, assistant UI
```

## License

[MIT](LICENSE)
