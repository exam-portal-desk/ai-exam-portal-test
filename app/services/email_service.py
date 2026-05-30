"""
app/services/email_service.py
Sends transactional emails via SMTP (Gmail).
Credentials: EMAIL_ADDRESS, EMAIL_PASSWORD in .env
"""

import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Tuple
from zoneinfo import ZoneInfo

import config


def _now_ist() -> str:
    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%B %d, %Y at %I:%M %p IST")


def _send(to_email: str, to_name: str, subject: str, html: str, text: str) -> Tuple[bool, str]:
    email_address = getattr(config, "EMAIL_ADDRESS", None)
    email_password = getattr(config, "EMAIL_PASSWORD", None)
    if not email_address or not email_password:
        return False, "SMTP credentials not configured"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"SmartAIExam <{email_address}>"
        msg["To"]      = f"{to_name} <{to_email}>"
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html",  "utf-8"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
            s.login(email_address, email_password)
            s.sendmail(email_address, to_email, msg.as_string())
        return True, "Email sent successfully"
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP auth failed - check EMAIL_ADDRESS and EMAIL_PASSWORD"
    except smtplib.SMTPRecipientsRefused:
        return False, f"Recipient refused: {to_email}"
    except Exception as e:
        return False, f"Email send failed: {e}"


def _logo_html(base_url: str) -> str:
    """Logo: image with text fallback. Works even if image blocked."""
    return f"""
    <table cellpadding="0" cellspacing="0" border="0" style="margin:0 auto 32px;">
      <tr>
        <td style="vertical-align:middle;padding-right:10px;">
          <img src="{base_url}/static/logo.png"
               alt="S" width="36" height="36"
               style="display:block;border-radius:8px;border:0;"
               onerror="this.style.display='none'">
        </td>
        <td style="vertical-align:middle;">
          <span style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
                       font-size:17px;font-weight:700;color:#111827;letter-spacing:-0.3px;">
            SmartAIExam
          </span>
        </td>
      </tr>
    </table>"""


def _email_wrapper(content: str, accent: str, footer_note: str, sent_at: str, base_url: str) -> str:
    logo = _logo_html(base_url)
    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>SmartAIExam</title>
  <!--[if mso]><style>table{{border-collapse:collapse}}</style><![endif]-->
</head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Helvetica Neue',Arial,sans-serif;-webkit-font-smoothing:antialiased;">

  <!-- Outer wrapper -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f4f5;min-height:100vh;">
    <tr>
      <td align="center" style="padding:40px 16px;">

        <!-- Card -->
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:580px;">

          <!-- Logo area (above card) -->
          <tr>
            <td align="center" style="padding-bottom:24px;">
              {logo}
            </td>
          </tr>

          <!-- Main card -->
          <tr>
            <td style="background:#ffffff;border-radius:16px;overflow:hidden;
                       box-shadow:0 1px 3px rgba(0,0,0,0.08),0 8px 32px rgba(0,0,0,0.06);">

              <!-- Accent top bar -->
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="height:4px;background:{accent};border-radius:16px 16px 0 0;font-size:0;line-height:0;">&nbsp;</td>
                </tr>
              </table>

              <!-- Card body -->
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="padding:40px 48px 48px;">
                    {content}
                  </td>
                </tr>
              </table>

            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td align="center" style="padding:28px 0 8px;">
              <p style="margin:0 0 6px;font-size:12px;color:#9ca3af;line-height:1.6;">
                <strong style="color:#6b7280;">SmartAIExam</strong>
                &nbsp;&middot;&nbsp;
                {sent_at}
              </p>
              <p style="margin:0;font-size:12px;color:#9ca3af;">{footer_note}</p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>

</body>
</html>"""


# ─────────────────────────────────────────────
# Password Setup Email
# ─────────────────────────────────────────────

def send_password_setup_email(
    email: str, full_name: str, username: str, token: str
) -> Tuple[bool, str]:
    setup_url  = f"{config.BASE_URL}/setup-password/{token}"
    sent_at    = _now_ist()
    first_name = full_name.split()[0] if full_name else "there"
    accent     = "linear-gradient(90deg,#22c55e,#16a34a)"

    content = f"""
      <!-- Heading -->
      <h1 style="margin:0 0 8px;font-size:24px;font-weight:700;color:#111827;letter-spacing:-0.5px;line-height:1.3;">
        Welcome, {first_name}!
      </h1>
      <p style="margin:0 0 32px;font-size:15px;color:#6b7280;line-height:1.6;">
        Your SmartAIExam account is ready. Set up your password to get started.
      </p>

      <!-- Credentials box -->
      <table width="100%" cellpadding="0" cellspacing="0" border="0"
             style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:12px;margin-bottom:32px;">
        <tr>
          <td style="padding:20px 24px 4px;">
            <p style="margin:0 0 16px;font-size:11px;font-weight:600;text-transform:uppercase;
                      letter-spacing:0.08em;color:#9ca3af;">Your Login Details</p>
          </td>
        </tr>
        <tr>
          <td style="padding:0 24px 8px;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="background:#fff;border:1px solid #e5e7eb;border-radius:8px;">
              <tr>
                <td style="padding:12px 16px;border-bottom:1px solid #f3f4f6;">
                  <span style="font-size:11px;font-weight:600;text-transform:uppercase;
                               letter-spacing:0.06em;color:#9ca3af;display:block;margin-bottom:3px;">Email</span>
                  <span style="font-size:14px;font-weight:600;color:#111827;
                               font-family:'SF Mono','Fira Code',Consolas,monospace;">{email}</span>
                </td>
              </tr>
              <tr>
                <td style="padding:12px 16px;">
                  <span style="font-size:11px;font-weight:600;text-transform:uppercase;
                               letter-spacing:0.06em;color:#9ca3af;display:block;margin-bottom:3px;">Username</span>
                  <span style="font-size:14px;font-weight:600;color:#111827;
                               font-family:'SF Mono','Fira Code',Consolas,monospace;">{username}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:8px 24px 20px;">
            <p style="margin:0;font-size:12px;color:#9ca3af;">You can log in with either your email or username.</p>
          </td>
        </tr>
      </table>

      <!-- CTA Button -->
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:32px;">
        <tr>
          <td align="center">
            <a href="{setup_url}"
               style="display:inline-block;background:linear-gradient(135deg,#22c55e,#16a34a);
                      color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;
                      padding:14px 40px;border-radius:10px;letter-spacing:-0.2px;
                      box-shadow:0 4px 14px rgba(34,197,94,0.35);">
              Set Up My Password &rarr;
            </a>
          </td>
        </tr>
      </table>

      <!-- Notice -->
      <table width="100%" cellpadding="0" cellspacing="0" border="0"
             style="background:#fafafa;border:1px solid #e5e7eb;border-radius:10px;">
        <tr>
          <td style="padding:16px 20px;">
            <p style="margin:0 0 8px;font-size:12px;font-weight:600;color:#374151;">Things to know</p>
            <p style="margin:0 0 5px;font-size:13px;color:#6b7280;line-height:1.6;">
              &bull;&nbsp; This link expires in <strong style="color:#374151;">1 hour</strong>
            </p>
            <p style="margin:0 0 5px;font-size:13px;color:#6b7280;line-height:1.6;">
              &bull;&nbsp; The link works <strong style="color:#374151;">only once</strong>
            </p>
            <p style="margin:0;font-size:13px;color:#6b7280;line-height:1.6;">
              &bull;&nbsp; Choose a strong password with <strong style="color:#374151;">10+ characters</strong>
            </p>
          </td>
        </tr>
      </table>"""

    html = _email_wrapper(
        content, accent,
        "If you didn't expect this email, you can safely ignore it.",
        sent_at, config.BASE_URL
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
    sent_at    = _now_ist()
    first_name = full_name.split()[0] if full_name else "there"
    accent     = "linear-gradient(90deg,#f59e0b,#d97706)"

    content = f"""
      <!-- Heading -->
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:32px;">
        <tr>
          <td style="background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:14px 18px;">
            <p style="margin:0;font-size:13px;color:#92400e;line-height:1.5;">
              <strong>Security notice:</strong> A password reset was requested for your account.
              If this wasn't you, ignore this email - your password will not change.
            </p>
          </td>
        </tr>
      </table>

      <h1 style="margin:0 0 8px;font-size:24px;font-weight:700;color:#111827;letter-spacing:-0.5px;line-height:1.3;">
        Reset your password
      </h1>
      <p style="margin:0 0 32px;font-size:15px;color:#6b7280;line-height:1.6;">
        Hi {first_name}, click the button below to choose a new password for your account.
      </p>

      <!-- Account box -->
      <table width="100%" cellpadding="0" cellspacing="0" border="0"
             style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:12px;margin-bottom:32px;">
        <tr>
          <td style="padding:20px 24px 8px;">
            <p style="margin:0 0 12px;font-size:11px;font-weight:600;text-transform:uppercase;
                      letter-spacing:0.08em;color:#9ca3af;">Account</p>
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="background:#fff;border:1px solid #e5e7eb;border-radius:8px;">
              <tr>
                <td style="padding:11px 16px;border-bottom:1px solid #f3f4f6;">
                  <span style="font-size:11px;font-weight:600;text-transform:uppercase;
                               letter-spacing:0.06em;color:#9ca3af;display:block;margin-bottom:3px;">Email</span>
                  <span style="font-size:14px;font-weight:600;color:#111827;
                               font-family:'SF Mono','Fira Code',Consolas,monospace;">{email}</span>
                </td>
              </tr>
              <tr>
                <td style="padding:11px 16px;">
                  <span style="font-size:11px;font-weight:600;text-transform:uppercase;
                               letter-spacing:0.06em;color:#9ca3af;display:block;margin-bottom:3px;">Username</span>
                  <span style="font-size:14px;font-weight:600;color:#111827;
                               font-family:'SF Mono','Fira Code',Consolas,monospace;">{username}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr><td style="padding:8px 24px 20px;">&nbsp;</td></tr>
      </table>

      <!-- CTA Button -->
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:32px;">
        <tr>
          <td align="center">
            <a href="{reset_url}"
               style="display:inline-block;background:linear-gradient(135deg,#ef4444,#dc2626);
                      color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;
                      padding:14px 40px;border-radius:10px;letter-spacing:-0.2px;
                      box-shadow:0 4px 14px rgba(239,68,68,0.35);">
              Reset My Password &rarr;
            </a>
          </td>
        </tr>
      </table>

      <!-- Notice -->
      <table width="100%" cellpadding="0" cellspacing="0" border="0"
             style="background:#fafafa;border:1px solid #e5e7eb;border-radius:10px;">
        <tr>
          <td style="padding:16px 20px;">
            <p style="margin:0 0 8px;font-size:12px;font-weight:600;color:#374151;">Things to know</p>
            <p style="margin:0 0 5px;font-size:13px;color:#6b7280;line-height:1.6;">
              &bull;&nbsp; This link expires in <strong style="color:#374151;">1 hour</strong>
            </p>
            <p style="margin:0 0 5px;font-size:13px;color:#6b7280;line-height:1.6;">
              &bull;&nbsp; The link works <strong style="color:#374151;">only once</strong>
            </p>
            <p style="margin:0;font-size:13px;color:#6b7280;line-height:1.6;">
              &bull;&nbsp; <strong style="color:#374151;">Never share</strong> this link with anyone
            </p>
          </td>
        </tr>
      </table>"""

    html = _email_wrapper(
        content, accent,
        "If you didn't request a password reset, no action is needed.",
        sent_at, config.BASE_URL
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