"""
app/db/dashboard_events.py
Generic unseen-event tracking shared by every Student Dashboard section that
needs read-state, EXCEPT chat (which reuses the existing chat_unread
mechanism — see app/db/chat.py — never duplicated here).
"""

from typing import Dict, Set
from app.db import fetch_all, execute


def get_seen_event_keys(user_id: int) -> Dict[str, Set[str]]:
    """One query for ALL of a user's seen events, grouped by event_type in
    Python — cheaper than one query per event_type."""
    try:
        rows = fetch_all(
            "SELECT event_type, event_key FROM dashboard_event_seen WHERE user_id=%s",
            (user_id,),
        )
    except Exception as e:
        print(f"[db.dashboard_events] get_seen_event_keys error: {e}")
        return {}

    out: Dict[str, Set[str]] = {}
    for r in rows:
        out.setdefault(r["event_type"], set()).add(str(r["event_key"]))
    return out


def mark_event_seen(user_id: int, event_type: str, event_key) -> None:
    """Idempotent — safe to call unconditionally from a view route."""
    try:
        execute(
            "INSERT INTO dashboard_event_seen (user_id, event_type, event_key) "
            "VALUES (%s,%s,%s) ON CONFLICT (user_id, event_type, event_key) DO NOTHING",
            (user_id, event_type, str(event_key)),
        )
    except Exception as e:
        print(f"[db.dashboard_events] mark_event_seen error: {e}")
