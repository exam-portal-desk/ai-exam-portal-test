"""
email_utils.py
Backward-compatibility shim.
Real logic is in app/services/email_service.py.
"""

from app.services.email_service import (  # noqa: F401
    send_password_setup_email,
    send_password_reset_email,
)

# Legacy helpers kept for any direct imports
from app.utils.helpers import generate_username, generate_password  # noqa: F401