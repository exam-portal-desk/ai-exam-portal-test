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

AI_DAILY_LIMIT = int(os.environ.get("AI_DAILY_LIMIT_PER_STUDENT", 50))
AI_MAX_MESSAGE_LENGTH = int(os.environ.get("AI_MAX_MESSAGE_LENGTH", 500))
AI_REQUEST_TIMEOUT = int(os.environ.get("AI_REQUEST_TIMEOUT", 30))
EXPLANATION_DAILY_LIMIT = int(os.environ.get("EXPLANATION_DAILY_LIMIT", 5))
EXPLANATION_PER_QUESTION_LIMIT = int(os.environ.get("EXPLANATION_PER_QUESTION_LIMIT", 2))

# ─────────────────────────────────────────────
# Email (SMTP - Gmail)
# ─────────────────────────────────────────────
MAILJET_API_KEY    = os.environ.get("MAILJET_API_KEY", "")
MAILJET_API_SECRET = os.environ.get("MAILJET_API_SECRET", "")
FROM_EMAIL         = os.environ.get("FROM_EMAIL", "")
FROM_NAME          = os.environ.get("SmartAIExamTest", "")
BASE_URL           = os.environ.get("BASE_URL", "https://your-domain.com")

# Public, permanent URL of the SmartAIExam logo asset (as uploaded to
# Supabase Storage), used in transactional emails so the logo doesn't
# depend on this app server's own /static/ route being reachable by the
# recipient's email client. Leave unset to fall back to {BASE_URL}/static/logo.png.
LOGO_ASSET_URL     = os.environ.get("LOGO_ASSET_URL", "")

# ─────────────────────────────────────────────
# Image upload — single shared cap for every image upload path (category
# images, subject/question bulk images, Notes attachments), so the limit
# can't drift out of sync between features.
# ─────────────────────────────────────────────
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_IMAGE_SIZE_KB = int(os.environ.get("MAX_IMAGE_SIZE_KB", 500))
MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_KB * 1024

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