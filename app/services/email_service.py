"""
app/services/email_service.py
Sends transactional emails via SMTP (Gmail).
Credentials: EMAIL_ADDRESS, EMAIL_PASSWORD in .env
"""

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Tuple

from flask import render_template

import app.config as config
from app.utils.datetime_service import now_app_tz


def _now_display() -> str:
    return now_app_tz().strftime(config.DISPLAY_DATETIME_FORMAT)


def _send(to_email: str, to_name: str, subject: str, html: str, text: str) -> Tuple[bool, str]:
    api_key    = getattr(config, "MAILJET_API_KEY", None)
    api_secret = getattr(config, "MAILJET_API_SECRET", None)
    from_email = getattr(config, "FROM_EMAIL", None)
    if not api_key or not api_secret or not from_email:
        return False, "Mailjet credentials not configured"
    try:
        from mailjet_rest import Client
        mj   = Client(auth=(api_key, api_secret), version="v3.1")
        data = {
            "Messages": [{
                "From": {"Email": from_email, "Name": "SmartAIExam"},
                "To":   [{"Email": to_email,  "Name": to_name}],
                "Subject":  subject,
                "TextPart": text,
                "HTMLPart": html,
            }]
        }
        result = mj.send.create(data=data)
        if result.status_code == 200:
            return True, "Email sent successfully"
        return False, f"Mailjet returned status {result.status_code}"
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