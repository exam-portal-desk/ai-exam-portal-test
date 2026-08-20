"""
app/services/email_service.py
Sends transactional emails through a generic HTTP email API — no provider
SDK, no vendor-specific payload shape hardcoded here. The auth header and
the JSON body shape are both driven entirely by config (see the Email
block in app/config.py) so switching providers is a config change, not a
code change. This module only knows how to: build headers from
EMAIL_SERVICE_AUTH_HEADER/AUTH_PREFIX, substitute {placeholders} into the
parsed EMAIL_SERVICE_PAYLOAD_TEMPLATE, and POST the result.
"""

import json
from typing import Any, Tuple

import requests
from flask import render_template

import app.config as config
from app.utils.datetime_service import now_app_tz


def _now_display() -> str:
    return now_app_tz().strftime(config.DISPLAY_DATETIME_FORMAT)


def _substitute(node: Any, context: dict) -> Any:
    """Recursively substitute {placeholder} tokens into a parsed JSON
    template's string leaves. Runs on already-parsed Python values (not
    raw JSON text), so subject/html/text content is safely JSON-escaped
    on output regardless of quotes/newlines/braces inside it. A leaf that
    references a placeholder not present in context (e.g. a provider
    field with no substitution) is left as-is rather than raising."""
    if isinstance(node, str):
        try:
            return node.format(**context)
        except (KeyError, IndexError):
            return node
    if isinstance(node, dict):
        return {k: _substitute(v, context) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, context) for v in node]
    return node


def _send(to_email: str, to_name: str, subject: str, html: str, text: str) -> Tuple[bool, str]:
    api_key    = getattr(config, "EMAIL_SERVICE_API_KEY", None)
    api_url    = getattr(config, "EMAIL_SERVICE_URL", None)
    from_email = getattr(config, "DEFAULT_FROM_EMAIL", None)
    if not api_key or not api_url or not from_email:
        return False, "Email service not configured"
    try:
        template = json.loads(config.EMAIL_SERVICE_PAYLOAD_TEMPLATE)
    except (json.JSONDecodeError, TypeError) as e:
        return False, f"EMAIL_SERVICE_PAYLOAD_TEMPLATE is not valid JSON: {e}"

    context = {
        "from_email": from_email, "to_email": to_email, "to_name": to_name or to_email,
        "subject": subject, "html": html, "text": text,
    }
    payload = _substitute(template, context)

    header_name = getattr(config, "EMAIL_SERVICE_AUTH_HEADER", "Authorization") or "Authorization"
    header_prefix = getattr(config, "EMAIL_SERVICE_AUTH_PREFIX", "Bearer ")
    headers = {"Content-Type": "application/json", header_name: f"{header_prefix}{api_key}"}

    try:
        # A fresh session with trust_env disabled so host-level proxy env vars
        # (common on some hosting platforms) can't silently intercept/break
        # outbound calls to the email API.
        session = requests.Session()
        session.trust_env = False
        resp = session.post(api_url, json=payload, headers=headers, timeout=15)
        if 200 <= resp.status_code < 300:
            return True, "Email sent successfully"
        return False, f"Email service returned status {resp.status_code}: {resp.text[:300]}"
    except Exception as e:
        return False, f"Email send failed: {e}"


def _logo_src(base_url: str) -> str:
    """Resolve the logo image URL for emails. Prefers the permanent Supabase
    Storage public URL (config.LOGO_ASSET_URL); falls back to the app-hosted
    path. <img> tags cannot render video, so a video LOGO_ASSET_URL is
    rejected in favor of the static fallback."""
    logo_src = config.LOGO_ASSET_URL or f"{base_url}/static/logo.png"
    _NON_IMAGE_EXTS = (".mp4", ".mov", ".webm", ".avi", ".mkv")
    if config.LOGO_ASSET_URL and config.LOGO_ASSET_URL.lower().split("?")[0].endswith(_NON_IMAGE_EXTS):
        print(f"[email_service] LOGO_ASSET_URL points at a video file ({config.LOGO_ASSET_URL}) "
              f"— <img> tags cannot render video in email clients. Falling back to /static/logo.png. "
              f"Set LOGO_ASSET_URL to a static PNG/JPG/GIF instead.")
        logo_src = f"{base_url}/static/logo.png"
    return logo_src


# ─────────────────────────────────────────────
# Password Setup Email
# ─────────────────────────────────────────────

def send_password_setup_email(
    email: str, full_name: str, username: str, token: str
) -> Tuple[bool, str]:
    setup_url  = f"{config.BASE_URL}/setup-password/{token}"
    sent_at    = _now_display()
    first_name = full_name.split()[0] if full_name else "there"

    html = render_template(
        "emails/password_setup.html",
        logo_src=_logo_src(config.BASE_URL), first_name=first_name,
        email=email, username=username, setup_url=setup_url, sent_at=sent_at,
    )

    text = (
        f"Welcome to SmartAIExam!\n\n"
        f"Hi {first_name},\n\n"
        f"Email:    {email}\nUsername: {username}\n\n"
        f"Set up your password: {setup_url}\n\n"
        f"Link expires in 1 hour, works once only.\n\n"
        f"- SmartAIExam | {sent_at}"
    )

    return _send(email, full_name, "Welcome to SmartAIExam - Complete Your Account Setup", html, text)


# ─────────────────────────────────────────────
# Password Reset Email
# ─────────────────────────────────────────────

def send_password_reset_email(
    email: str, full_name: str, username: str, token: str
) -> Tuple[bool, str]:
    reset_url  = f"{config.BASE_URL}/reset-password/{token}"
    sent_at    = _now_display()
    first_name = full_name.split()[0] if full_name else "there"

    html = render_template(
        "emails/password_reset.html",
        logo_src=_logo_src(config.BASE_URL), first_name=first_name,
        email=email, username=username, reset_url=reset_url, sent_at=sent_at,
    )

    text = (
        f"Reset Your SmartAIExam Password\n\n"
        f"Hi {first_name},\n\n"
        f"Email:    {email}\nUsername: {username}\n\n"
        f"Reset your password: {reset_url}\n\n"
        f"Link expires in 1 hour, works once only.\n"
        f"If you didn't request this, ignore this email.\n\n"
        f"- SmartAIExam | {sent_at}"
    )

    return _send(email, full_name, "Reset Your SmartAIExam Password", html, text)