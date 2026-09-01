"""
config.py
Centralized configuration — all os.environ.get() calls live here.
Import from this module everywhere instead of reading env vars directly.
"""

import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# Core Flask
# ─────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
IS_PRODUCTION = os.environ.get("RENDER") is not None
DEBUG = not IS_PRODUCTION

# ─────────────────────────────────────────────
# Date/Time — central timezone + display format for the whole app.
# Storage always uses UTC; this only controls what users see.
# ─────────────────────────────────────────────
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Kolkata")
DISPLAY_DATETIME_FORMAT = os.environ.get("DISPLAY_DATETIME_FORMAT", "%d %B %Y %I:%M %p")
DISPLAY_DATE_FORMAT = os.environ.get("DISPLAY_DATE_FORMAT", "%d %B %Y")

# ─────────────────────────────────────────────
# Session
# ─────────────────────────────────────────────
import tempfile

SESSION_TYPE = os.environ.get("SESSION_TYPE", "filesystem")
SESSION_FILE_DIR = os.environ.get("SESSION_FILE_DIR", os.path.join(tempfile.gettempdir(), "flask_session"),)
PERMANENT_SESSION_LIFETIME = timedelta(seconds=int(os.environ.get("PERMANENT_SESSION_LIFETIME", 10800)))
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = os.environ.get("FORCE_SECURE_COOKIES", "1") == "1"

# ─────────────────────────────────────────────
# Database (PostgreSQL — provider independent)
# ─────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_POOL_MIN = int(os.environ.get("DB_POOL_MIN", 1))
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", 10))

# ─────────────────────────────────────────────
# Auto-submit sweep (Scheduled Exam deadline enforcement) — see
# app/services/auto_submit_service.py. Each tick claims and finalizes up
# to AUTO_SUBMIT_BATCH_SIZE overdue attempts using short, independent DB
# round trips (never one long-held connection/transaction for the whole
# batch), so raising the batch size mainly trades sweep-tick duration for
# fewer ticks — it does not hold the pool open for longer per attempt.
AUTO_SUBMIT_SWEEP_INTERVAL_SECONDS = int(os.environ.get("AUTO_SUBMIT_SWEEP_INTERVAL_SECONDS", 5))
AUTO_SUBMIT_BATCH_SIZE = int(os.environ.get("AUTO_SUBMIT_BATCH_SIZE", 200))

# ─────────────────────────────────────────────
# Object storage — provider selected by STORAGE_BACKEND (local | s3).
# S3 backend works with any S3-compatible provider (AWS S3, Cloudflare R2,
# MinIO, Supabase Storage's S3 interface, ...) via boto3, never a vendor SDK.
# ─────────────────────────────────────────────
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")

STORAGE_LOCAL_ROOT = os.environ.get("STORAGE_LOCAL_ROOT", "./storage")
STORAGE_LOCAL_URL_PREFIX = os.environ.get("STORAGE_LOCAL_URL_PREFIX", "/notes/asset-file")

STORAGE_BUCKET = os.environ.get("STORAGE_BUCKET", "")
STORAGE_ENDPOINT_URL = os.environ.get("STORAGE_ENDPOINT_URL", "")
STORAGE_REGION = os.environ.get("STORAGE_REGION", "")
STORAGE_ACCESS_KEY = os.environ.get("STORAGE_ACCESS_KEY", "")
STORAGE_SECRET_KEY = os.environ.get("STORAGE_SECRET_KEY", "")

# Notes module
NOTES_TRASH_RETENTION_DAYS = int(os.environ.get("NOTES_TRASH_RETENTION_DAYS", 30))
NOTES_TRASH_CLEANUP_ENABLED = os.environ.get("NOTES_TRASH_CLEANUP_ENABLED", "1") == "1"

# ─────────────────────────────────────────────
# Google OAuth (Sign in with Google)
# ─────────────────────────────────────────────
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")

# ─────────────────────────────────────────────
# AI — model registry (config/ai_models.json) selects provider/model/key;
# these only pick which registry entry is active per flow.
# ─────────────────────────────────────────────
ASSISTANT_TEXT_MODEL = os.environ.get("ASSISTANT_TEXT_MODEL", "assistant-default")
EXPLANATION_TEXT_MODEL = os.environ.get("EXPLANATION_TEXT_MODEL", "explanation-default")
EXPLANATION_VISION_MODEL_NAME = os.environ.get("EXPLANATION_VISION_MODEL_NAME", "explanation-vision-default")
QUESTION_GENERATOR_TEXT_MODEL = os.environ.get("QUESTION_GENERATOR_TEXT_MODEL", "question-generator-default")
QUESTION_GENERATOR_VISION_MODEL = os.environ.get("QUESTION_GENERATOR_VISION_MODEL", "question-generator-vision-default")

AI_DAILY_LIMIT = int(os.environ.get("AI_DAILY_LIMIT_PER_STUDENT", 50))
AI_MAX_MESSAGE_LENGTH = int(os.environ.get("AI_MAX_MESSAGE_LENGTH", 500))
AI_REQUEST_TIMEOUT = int(os.environ.get("AI_REQUEST_TIMEOUT", 30))
AI_CONTEXT_RECENT_MESSAGES = int(os.environ.get("AI_CONTEXT_RECENT_MESSAGES", 12))
MAX_MESSAGES_PER_CONVERSATION = int(os.environ.get("MAX_MESSAGES_PER_CONVERSATION", 100))
AI_TITLE_MAX_TOKENS = int(os.environ.get("AI_TITLE_MAX_TOKENS", 300))
AI_TITLE_TEMPERATURE = float(os.environ.get("AI_TITLE_TEMPERATURE", 0.2))
EXPLANATION_DAILY_LIMIT = int(os.environ.get("EXPLANATION_DAILY_LIMIT", 5))
EXPLANATION_PER_QUESTION_LIMIT = int(os.environ.get("EXPLANATION_PER_QUESTION_LIMIT", 2))

# ─────────────────────────────────────────────
# Email — generic HTTP email API, provider-independent (no vendor SDK).
# Switching providers requires ONLY changing these env vars, never code:
#   EMAIL_SERVICE_URL/API_KEY/DEFAULT_FROM_EMAIL — endpoint + credentials.
#   EMAIL_SERVICE_AUTH_HEADER/AUTH_PREFIX         — how the key is sent,
#     e.g. Authorization/"Bearer " (default) or api-key/"" for providers
#     that want the raw key in a custom header.
#   EMAIL_SERVICE_PAYLOAD_TEMPLATE                — the exact JSON body
#     shape the provider expects, as a JSON string with placeholders
#     {from_email} {to_email} {to_name} {subject} {html} {text}. Parsed
#     once here; app/services/email_service.py substitutes placeholders
#     into the parsed structure (safe against quotes/newlines in content)
#     and POSTs it as-is — it has no knowledge of any provider's shape.
# ─────────────────────────────────────────────
EMAIL_SERVICE_API_KEY = os.environ.get("EMAIL_SERVICE_API_KEY", "")
EMAIL_SERVICE_URL     = os.environ.get("EMAIL_SERVICE_URL", "")
DEFAULT_FROM_EMAIL    = os.environ.get("DEFAULT_FROM_EMAIL", "")
EMAIL_SERVICE_AUTH_HEADER = os.environ.get("EMAIL_SERVICE_AUTH_HEADER", "Authorization")
EMAIL_SERVICE_AUTH_PREFIX = os.environ.get("EMAIL_SERVICE_AUTH_PREFIX", "Bearer ")
EMAIL_SERVICE_PAYLOAD_TEMPLATE = os.environ.get(
    "EMAIL_SERVICE_PAYLOAD_TEMPLATE",
    '{"from": "{from_email}", "to": "{to_email}", "subject": "{subject}", "html": "{html}", "text": "{text}"}',
)
BASE_URL              = os.environ.get("BASE_URL", "https://your-domain.com")

# Public, permanent URL of the SmartAIExam logo asset (as uploaded to
# Supabase Storage), used in transactional emails so the logo doesn't
# depend on this app server's own /static/ route being reachable by the
# recipient's email client. Leave unset to fall back to {BASE_URL}/static/logo.png.
LOGO_ASSET_URL     = os.environ.get("LOGO_ASSET_URL", "")

# ─────────────────────────────────────────────
# Landing page — "Founder / CEO" section (templates/index.html).
# CEO_IMAGE_KEY is a STORAGE KEY (e.g. "Profile/founder.jpg"), never a raw
# URL — resolved server-side and streamed through the app's own public,
# read-only proxy route (see /assets/ceo-photo in app/routes/web/misc.py),
# the same "never hand the browser a raw storage/bucket URL" pattern
# app/services/image_storage_service.py already uses everywhere else.
# Leave blank until a photo has been uploaded to the storage bucket under
# this key — the landing page falls back to a placeholder rather than a
# broken image.
# ─────────────────────────────────────────────
CEO_NAME       = os.environ.get("CEO_NAME", "")
CEO_TITLE      = os.environ.get("CEO_TITLE", "Founder & CEO, SmartAIExam")
CEO_IMAGE_KEY  = os.environ.get("CEO_IMAGE_KEY", "")

def _public_env(name: str) -> str:
    """Same as os.environ.get(name, ""), except a value that's just a
    Python-flavored "empty" placeholder — none/null/undefined/n/a, in any
    case — is treated as blank too. Guards against exactly the mistake of
    typing PUBLIC_ADDRESS=None in .env (a real, non-empty string as far as
    os.environ is concerned) and having every "leave blank to hide this"
    template literally print the word "None" instead of hiding the field."""
    val = (os.environ.get(name) or "").strip()
    if val.lower() in ("none", "null", "undefined", "n/a", "na"):
        return ""
    return val


# ─────────────────────────────────────────────
# Public-facing contact / footer info — read by every footer (landing page,
# user portal, admin portal) and by the legal/contact/about/support pages,
# via the inject_globals() context processor in app/__init__.py so no
# template needs its own hardcoded copy. Every field defaults to blank
# rather than a placeholder-looking value — templates hide the
# corresponding UI element entirely when a field is blank instead of ever
# showing a fake email/phone/address/social link. Only set a real value in
# .env for something that's actually true; leave the rest unset.
# ─────────────────────────────────────────────
PUBLIC_SUPPORT_EMAIL   = _public_env("PUBLIC_SUPPORT_EMAIL")
PUBLIC_CONTACT_PHONE   = _public_env("PUBLIC_CONTACT_PHONE")
PUBLIC_ADDRESS         = _public_env("PUBLIC_ADDRESS")
PUBLIC_SOCIAL_TWITTER  = _public_env("PUBLIC_SOCIAL_TWITTER")
PUBLIC_SOCIAL_LINKEDIN = _public_env("PUBLIC_SOCIAL_LINKEDIN")
PUBLIC_SOCIAL_GITHUB   = _public_env("PUBLIC_SOCIAL_GITHUB")
PUBLIC_SOCIAL_INSTAGRAM = _public_env("PUBLIC_SOCIAL_INSTAGRAM")

# Per-policy "Last updated" dates — each policy can genuinely change on its
# own schedule, so these are independent. Left blank until a real date is
# known; the templates omit the line entirely rather than show a
# potentially-misleading date.
LEGAL_PRIVACY_LAST_UPDATED = _public_env("LEGAL_PRIVACY_LAST_UPDATED")
LEGAL_TERMS_LAST_UPDATED = _public_env("LEGAL_TERMS_LAST_UPDATED")
LEGAL_ACCOUNT_DELETION_LAST_UPDATED = _public_env("LEGAL_ACCOUNT_DELETION_LAST_UPDATED")

# ─────────────────────────────────────────────
# OTP — "Existing Active Session" login verification. When a user logs in
# correctly but already has another active session, they must verify a
# mailed one-time code before the old session is invalidated. See
# app/routes/web/auth.py (verify_session / verify_session_resend).
# ─────────────────────────────────────────────
OTP_LENGTH = int(os.environ.get("OTP_LENGTH", 6))
OTP_EXPIRY_SECONDS = int(os.environ.get("OTP_EXPIRY_SECONDS", 600))
OTP_MAX_REQUESTS = int(os.environ.get("OTP_MAX_REQUESTS", 5))
OTP_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("OTP_RATE_LIMIT_WINDOW_SECONDS", 900))
OTP_RESEND_COOLDOWN_SECONDS = int(os.environ.get("OTP_RESEND_COOLDOWN_SECONDS", 60))
OTP_MAX_VERIFY_ATTEMPTS = int(os.environ.get("OTP_MAX_VERIFY_ATTEMPTS", 5))

# ─────────────────────────────────────────────
# Image upload — single shared cap for every image upload path (category
# images, subject/question bulk images, Notes attachments), so the limit
# can't drift out of sync between features.
# ─────────────────────────────────────────────
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_IMAGE_SIZE_KB = int(os.environ.get("MAX_IMAGE_SIZE_KB", 500))
MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_KB * 1024

# Profile photos use a smaller, distinct cap from the shared image limit above.
MAX_PROFILE_PHOTO_SIZE_KB = int(os.environ.get("MAX_PROFILE_PHOTO_SIZE_KB", 200))

# Chat background images can be larger than an avatar but stay capped for performance.
MAX_CHAT_BACKGROUND_SIZE_KB = int(os.environ.get("MAX_CHAT_BACKGROUND_SIZE_KB", 500))

# ─────────────────────────────────────────────
# Cache settings
# ─────────────────────────────────────────────
CACHE_DEFAULT_TTL = 300          # 5 minutes
CACHE_EXAM_DATA_TTL = 300
CACHE_AI_LIMITS_TTL = 30
CACHE_MAX_ITEMS = 100

# ─────────────────────────────────────────────
# Upload temp dir
# Project-root-relative (this file now lives in app/, one level deeper than when this
# path was first written, hence the extra dirname() — same project-root convention
# app/__init__.py already uses for template_folder/static_folder).
# ─────────────────────────────────────────────
UPLOAD_TMP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads_tmp")
os.makedirs(UPLOAD_TMP_DIR, exist_ok=True)