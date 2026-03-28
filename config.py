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
# Session
# ─────────────────────────────────────────────
import tempfile

SESSION_TYPE = os.environ.get("SESSION_TYPE", "filesystem")
SESSION_FILE_DIR = os.environ.get(
    "SESSION_FILE_DIR",
    os.path.join(tempfile.gettempdir(), "flask_session"),
)
PERMANENT_SESSION_LIFETIME = timedelta(
    seconds=int(os.environ.get("PERMANENT_SESSION_LIFETIME", 10800))
)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = os.environ.get("FORCE_SECURE_COOKIES", "1") == "1"

# ─────────────────────────────────────────────
# Supabase
# ─────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# ─────────────────────────────────────────────
# Google Drive — service account + user OAuth
# ─────────────────────────────────────────────
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_SERVICE_TOKEN_JSON = os.environ.get("GOOGLE_SERVICE_TOKEN_JSON", "token.json")
GOOGLE_OAUTH_CLIENT_JSON = os.environ.get("GOOGLE_OAUTH_CLIENT_JSON", "")

# ─────────────────────────────────────────────
# Google OAuth (Sign in with Google)
# ─────────────────────────────────────────────
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")

# Drive folder IDs
ROOT_FOLDER_ID = os.environ.get("ROOT_FOLDER_ID", "")
IMAGES_FOLDER_ID = os.environ.get("IMAGES_FOLDER_ID", "")

DRIVE_FOLDER_IDS = {
    "root": ROOT_FOLDER_ID,
    "images": IMAGES_FOLDER_ID
}

# ─────────────────────────────────────────────
# Subjects Drive file (legacy env, kept for compat)
# ─────────────────────────────────────────────
SUBJECTS_FILE_ID = os.environ.get("SUBJECTS_FILE_ID", "")

# ─────────────────────────────────────────────
# AI / Groq
# ─────────────────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
AI_MODEL_NAME = os.environ.get("AI_MODEL_NAME", "llama-3.3-70b-versatile")
AI_DAILY_LIMIT = int(os.environ.get("AI_DAILY_LIMIT_PER_STUDENT", 50))
AI_MAX_MESSAGE_LENGTH = int(os.environ.get("AI_MAX_MESSAGE_LENGTH", 500))
AI_REQUEST_TIMEOUT = int(os.environ.get("AI_REQUEST_TIMEOUT", 30))

# ─────────────────────────────────────────────
# Gemini (AI question generator)
# ─────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-1.5-flash")
DRIVE_CATEGORY_FOLDER_ID = os.environ.get("DRIVE_CATEGORY_FOLDER_ID", "")

# ─────────────────────────────────────────────
# Email (Mailjet)
# ─────────────────────────────────────────────
MAILJET_API_KEY = os.environ.get("MAILJET_API_KEY", "")
MAILJET_API_SECRET = os.environ.get("MAILJET_API_SECRET", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@examportal.com")
FROM_NAME = "ExamPortal System"
BASE_URL = os.environ.get("BASE_URL", "https://your-domain.com")

# ─────────────────────────────────────────────
# Image upload
# ─────────────────────────────────────────────
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_FILE_SIZE_MB = 15

# ─────────────────────────────────────────────
# Cache settings
# ─────────────────────────────────────────────
CACHE_DEFAULT_TTL = 300          # 5 minutes
CACHE_EXAM_DATA_TTL = 300
CACHE_AI_LIMITS_TTL = 30
CACHE_MAX_ITEMS = 100
REDIS_URL = os.environ.get("REDIS_URL", "")

# ─────────────────────────────────────────────
# Upload temp dir
# ─────────────────────────────────────────────
UPLOAD_TMP_DIR = os.path.join(os.path.dirname(__file__), "uploads_tmp")
os.makedirs(UPLOAD_TMP_DIR, exist_ok=True)