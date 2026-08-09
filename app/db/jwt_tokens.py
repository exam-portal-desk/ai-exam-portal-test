"""
app/db/jwt_tokens.py
PostgreSQL queries for the `jwt_refresh_tokens` table.
"""

from typing import Optional, Dict
from app.db import fetch_one, execute, insert_returning


def create_refresh_token(user_id: int, token: str, expires_at: str) -> None:
    insert_returning("jwt_refresh_tokens", {
        "user_id": user_id, "token": token, "expires_at": expires_at, "revoked": False,
    })


def get_refresh_token(token: str) -> Optional[Dict]:
    return fetch_one("SELECT * FROM jwt_refresh_tokens WHERE token=%s", (token,))


def revoke_refresh_token(token: str) -> None:
    execute("UPDATE jwt_refresh_tokens SET revoked=%s WHERE token=%s", (True, token))
