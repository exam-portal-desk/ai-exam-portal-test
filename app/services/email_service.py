"""
app/services/email_service.py
Sends transactional emails via Mailjet.
Moved and consolidated from email_utils.py.
"""

from datetime import datetime
from typing import Tuple

import config


def _get_mailjet():
    from mailjet_rest import Client
    return Client(
        auth=(config.MAILJET_API_KEY, config.MAILJET_API_SECRET),
        version="v3.1",
    )


def _send(to_email: str, to_name: str, subject: str, html: str, text: str) -> Tuple[bool, str]:
    """Low-level send via Mailjet. Returns (success, message)."""
    if not config.MAILJET_API_KEY or not config.MAILJET_API_SECRET:
        return False, "Mailjet credentials not configured"

    try:
        mj = _get_mailjet()
        data = {
            "Messages": [
                {
                    "From": {"Email": config.FROM_EMAIL, "Name": config.FROM_NAME},
                    "To": [{"Email": to_email, "Name": to_name}],
                    "Subject": subject,
                    "TextPart": text,
                    "HTMLPart": html,
                }
            ]
        }
        result = mj.send.create(data=data)
        if result.status_code == 200:
            return True, "Email sent successfully"
        return False, f"Mailjet returned status {result.status_code}"
    except RecursionError as e:
        return False, f"RecursionError: {e}"
    except Exception as e:
        return False, f"Email send failed: {e}"


# ─────────────────────────────────────────────
# Password Setup Email
# ─────────────────────────────────────────────

def send_password_setup_email(
    email: str, full_name: str, username: str, token: str
) -> Tuple[bool, str]:
    setup_url = f"{config.BASE_URL}/setup-password/{token}"
    sent_at = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
body{{font-family:'Segoe UI',sans-serif;line-height:1.6;color:#333;max-width:600px;margin:0 auto}}
.header{{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:30px;text-align:center;border-radius:12px 12px 0 0}}
.content{{background:#fff;padding:40px 30px;border:1px solid #e5e7eb}}
.cred-box{{background:#f8f9ff;border:2px solid #e5e7eb;border-radius:8px;padding:20px;margin:20px 0;text-align:center}}
.cred-item{{margin:10px 0;padding:8px;background:#fff;border-radius:6px;border-left:4px solid #667eea}}
.cred-label{{font-weight:600;color:#4b5563;font-size:14px}}
.cred-value{{font-family:monospace;font-size:16px;color:#1f2937;font-weight:600}}
.btn{{display:inline-block;background:linear-gradient(135deg,#10b981,#059669);color:#fff;padding:15px 35px;text-decoration:none;border-radius:8px;font-weight:600;font-size:16px;margin:20px 0}}
.footer{{background:#f9fafb;padding:20px;text-align:center;color:#6b7280;font-size:14px;border-radius:0 0 12px 12px}}
</style></head><body>
<div class="header"><h1>Welcome to ExamPortal!</h1><p>Your account has been created successfully</p></div>
<div class="content">
<h2>Hello {full_name}!</h2>
<p>Your account has been created. Here are your login credentials:</p>
<div class="cred-box">
<h3>Your Login Credentials</h3>
<div class="cred-item"><div class="cred-label">Email</div><div class="cred-value">{email}</div></div>
<div class="cred-item"><div class="cred-label">Username</div><div class="cred-value">{username}</div></div>
<p style="color:#6b7280;font-size:14px">You can login with either your email or username</p>
</div>
<p>Please set up your password using the secure link below:</p>
<div style="text-align:center"><a href="{setup_url}" class="btn">Set Up Your Password</a></div>
<ul>
<li>This link expires in <strong>1 hour</strong></li>
<li>You can only use this link once</li>
<li>Choose a strong password (10+ characters)</li>
</ul>
</div>
<div class="footer"><p><strong>ExamPortal System</strong></p><p>Sent on {sent_at} · Link expires in 1 hour</p></div>
</body></html>"""

    text = (
        f"Welcome to ExamPortal!\n\nHello {full_name},\n\n"
        f"Email: {email}\nUsername: {username}\n\n"
        f"Set up your password: {setup_url}\n\n"
        f"Link expires in 1 hour.\n\nExamPortal Team"
    )

    return _send(email, full_name, "Welcome to ExamPortal — Complete Your Account Setup", html, text)


# ─────────────────────────────────────────────
# Password Reset Email
# ─────────────────────────────────────────────

def send_password_reset_email(
    email: str, full_name: str, username: str, token: str
) -> Tuple[bool, str]:
    reset_url = f"{config.BASE_URL}/reset-password/{token}"
    sent_at = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
body{{font-family:'Segoe UI',sans-serif;line-height:1.6;color:#333;max-width:600px;margin:0 auto}}
.header{{background:linear-gradient(135deg,#f59e0b,#d97706);color:#fff;padding:30px;text-align:center;border-radius:12px 12px 0 0}}
.content{{background:#fff;padding:40px 30px;border:1px solid #e5e7eb}}
.cred-box{{background:#fff7ed;border:2px solid #fed7aa;border-radius:8px;padding:20px;margin:20px 0;text-align:center}}
.cred-item{{margin:10px 0;padding:8px;background:#fff;border-radius:6px;border-left:4px solid #f59e0b}}
.cred-label{{font-weight:600;color:#4b5563;font-size:14px}}
.cred-value{{font-family:monospace;font-size:16px;color:#1f2937;font-weight:600}}
.btn{{display:inline-block;background:linear-gradient(135deg,#dc2626,#b91c1c);color:#fff;padding:15px 35px;text-decoration:none;border-radius:8px;font-weight:600;font-size:16px;margin:20px 0}}
.footer{{background:#f9fafb;padding:20px;text-align:center;color:#6b7280;font-size:14px;border-radius:0 0 12px 12px}}
</style></head><body>
<div class="header"><h1>Password Reset Request</h1><p>Reset your ExamPortal password</p></div>
<div class="content">
<h2>Hello {full_name}!</h2>
<p>We received a password reset request for your account:</p>
<div class="cred-box">
<div class="cred-item"><div class="cred-label">Email</div><div class="cred-value">{email}</div></div>
<div class="cred-item"><div class="cred-label">Username</div><div class="cred-value">{username}</div></div>
</div>
<div style="text-align:center"><a href="{reset_url}" class="btn">Reset Your Password</a></div>
<ul>
<li>This link expires in <strong>1 hour</strong></li>
<li>If you didn't request this, ignore this email</li>
</ul>
</div>
<div class="footer"><p><strong>ExamPortal System</strong></p><p>Sent on {sent_at} · Link expires in 1 hour</p></div>
</body></html>"""

    text = (
        f"Password Reset — ExamPortal\n\nHello {full_name},\n\n"
        f"Email: {email}\nUsername: {username}\n\n"
        f"Reset your password: {reset_url}\n\n"
        f"Link expires in 1 hour. If you didn't request this, ignore this email.\n\nExamPortal Team"
    )

    return _send(email, full_name, "Reset Your ExamPortal Password", html, text)