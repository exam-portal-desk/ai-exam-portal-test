"""
app/db/ai.py
PostgreSQL queries for ai_conversations, ai_chat_history, ai_usage_tracking
and ai_generation_jobs tables.
"""

from typing import Optional, List, Dict
from psycopg2.extras import Json
from app.db import fetch_one, fetch_all, execute, execute_returning, insert_returning, set_clause
from app.utils.datetime_service import now_utc_naive, today_app_date

# app/db/__init__.py registers an adapter for plain dict -> Json, but NOT for
# list — a bare Python list passed as a query param would otherwise be sent
# as a Postgres ARRAY literal, not JSONB, and fail against a jsonb column.
# Wrap list-typed JSONB values explicitly; dicts pass through unchanged
# (the registered adapter already handles them) but wrapping is harmless.
_JSONB_JOB_FIELDS = ("config", "batch_configs", "batches", "questions")


def _wrap_jsonb(fields: dict) -> dict:
    return {
        k: (Json(v) if k in _JSONB_JOB_FIELDS and v is not None else v)
        for k, v in fields.items()
    }


# ─────────────────────────────────────────────
# Conversations
# ─────────────────────────────────────────────

def list_conversations(user_id: int, limit: int = 20, offset: int = 0, search: Optional[str] = None) -> List[Dict]:
    """Fetches limit+1 rows so the caller can tell if there's another page
    without a separate COUNT(*) query — trim the extra row before display."""
    try:
        if search:
            like = f"%{search}%"
            return fetch_all(
                "SELECT id,user_id,title,message_count,created_at,updated_at FROM ai_conversations "
                "WHERE user_id=%s AND (title ILIKE %s OR EXISTS ("
                "  SELECT 1 FROM ai_chat_history h WHERE h.conversation_id=ai_conversations.id AND h.message ILIKE %s"
                ")) ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s",
                (user_id, like, like, limit + 1, offset),
            )
        return fetch_all(
            "SELECT id,user_id,title,message_count,created_at,updated_at FROM ai_conversations "
            "WHERE user_id=%s ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s",
            (user_id, limit + 1, offset),
        )
    except Exception as e:
        print(f"[db.ai] list_conversations error: {e}")
        return []


def get_owned_conversation(conversation_id: int, user_id: int) -> Optional[Dict]:
    try:
        return fetch_one(
            "SELECT id,user_id,title,message_count,created_at,updated_at FROM ai_conversations "
            "WHERE id=%s AND user_id=%s",
            (conversation_id, user_id),
        )
    except Exception as e:
        print(f"[db.ai] get_owned_conversation error: {e}")
        return None


def create_conversation(user_id: int, title: str = "New Chat") -> Optional[Dict]:
    try:
        return insert_returning("ai_conversations", {"user_id": user_id, "title": title})
    except Exception as e:
        print(f"[db.ai] create_conversation error: {e}")
        return None


def rename_conversation(conversation_id: int, user_id: int, title: str) -> Optional[Dict]:
    try:
        rows = execute_returning(
            "UPDATE ai_conversations SET title=%s WHERE id=%s AND user_id=%s RETURNING *",
            (title, conversation_id, user_id),
        )
        return rows[0] if rows else None
    except Exception as e:
        print(f"[db.ai] rename_conversation error: {e}")
        return None


def delete_conversation(conversation_id: int, user_id: int) -> bool:
    try:
        rows = execute_returning(
            "DELETE FROM ai_conversations WHERE id=%s AND user_id=%s RETURNING id",
            (conversation_id, user_id),
        )
        return bool(rows)
    except Exception as e:
        print(f"[db.ai] delete_conversation error: {e}")
        return False


def touch_and_count_conversation(conversation_id: int) -> Optional[int]:
    """Atomically bump message_count and updated_at in a single round trip."""
    try:
        rows = execute_returning(
            "UPDATE ai_conversations SET message_count = message_count + 1, updated_at = %s "
            "WHERE id=%s RETURNING message_count",
            (now_utc_naive().strftime("%Y-%m-%d %H:%M:%S"), conversation_id),
        )
        return int(rows[0]["message_count"]) if rows else None
    except Exception as e:
        print(f"[db.ai] touch_and_count_conversation error: {e}")
        return None


# ─────────────────────────────────────────────
# Chat History (per-conversation)
# ─────────────────────────────────────────────

def get_conversation_messages(conversation_id: int, user_id: int, limit: int = 30, offset: int = 0) -> List[Dict]:
    """Fetches limit+1 rows (newest first) so the caller can detect another page
    without a COUNT(*) query. Ownership-checked via EXISTS against ai_conversations."""
    try:
        return fetch_all(
            "SELECT h.id,h.conversation_id,h.message,h.is_user,h.timestamp FROM ai_chat_history h "
            "WHERE h.conversation_id=%s AND EXISTS ("
            "  SELECT 1 FROM ai_conversations c WHERE c.id=h.conversation_id AND c.user_id=%s"
            ") ORDER BY h.id DESC LIMIT %s OFFSET %s",
            (conversation_id, user_id, limit + 1, offset),
        )
    except Exception as e:
        print(f"[db.ai] get_conversation_messages error: {e}")
        return []


def save_chat_message(user_id: int, conversation_id: int, message: str, is_user: bool) -> bool:
    try:
        insert_returning("ai_chat_history", {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "message": message,
            "is_user": is_user,
            "timestamp": now_utc_naive().strftime("%Y-%m-%d %H:%M:%S"),
        })
        return True
    except Exception as e:
        print(f"[db.ai] save_chat_message error: {e}")
        return False


def get_history_for_context(conversation_id: int, last_n: int) -> List[Dict]:
    try:
        return fetch_all(
            "SELECT message,is_user,id FROM ai_chat_history WHERE conversation_id=%s "
            "ORDER BY id DESC LIMIT %s",
            (conversation_id, last_n),
        )
    except Exception as e:
        print(f"[db.ai] get_history_for_context error: {e}")
        return []


# ─────────────────────────────────────────────
# Usage Tracking
# ─────────────────────────────────────────────

def get_today_usage(user_id: int) -> Optional[Dict]:
    try:
        today = today_app_date()
        return fetch_one(
            "SELECT id,user_id,date,questions_used FROM ai_usage_tracking WHERE user_id=%s AND date=%s",
            (user_id, today),
        )
    except Exception as e:
        print(f"[db.ai] get_today_usage error: {e}")
        return None


def increment_usage(user_id: int) -> bool:
    """Upsert today's usage count — single round-trip."""
    try:
        today = today_app_date()
        existing = fetch_one(
            "SELECT id,questions_used FROM ai_usage_tracking WHERE user_id=%s AND date=%s",
            (user_id, today),
        )

        if existing:
            execute(
                "UPDATE ai_usage_tracking SET questions_used=%s WHERE id=%s",
                (int(existing.get("questions_used", 0)) + 1, existing["id"]),
            )
        else:
            insert_returning("ai_usage_tracking", {"user_id": user_id, "date": today, "questions_used": 1})

        return True
    except Exception as e:
        print(f"[db.ai] increment_usage error: {e}")
        return False


# ─────────────────────────────────────────────
# AI Generation Jobs (AI Command Centre — durable write-through layer
# under the in-memory _jobs dict in app/routes/api/v01/admin/ai_centre.py;
# see migrations/20260831_ai_generation_jobs.sql for why this table exists)
# ─────────────────────────────────────────────

def create_generation_job(job_id: str, admin_id: int, exam_id: int, exam_name: str,
                           mode: str, config: dict, batch_configs: list) -> bool:
    try:
        insert_returning("ai_generation_jobs", _wrap_jsonb({
            "id": job_id,
            "admin_id": admin_id,
            "exam_id": exam_id,
            "exam_name": exam_name,
            "mode": mode,
            "status": "queued",
            "config": config,
            "batch_configs": batch_configs,
        }))
        return True
    except Exception as e:
        print(f"[db.ai] create_generation_job error: {e}")
        return False


def update_generation_job(job_id: str, **fields) -> bool:
    """Partial update of a job row — pass any subset of its columns as kwargs."""
    if not fields:
        return True
    try:
        fields = _wrap_jsonb(fields)
        fields["updated_at"] = now_utc_naive().strftime("%Y-%m-%d %H:%M:%S")
        clause, params = set_clause(fields)
        execute(f"UPDATE ai_generation_jobs SET {clause} WHERE id=%s", params + [job_id])
        return True
    except Exception as e:
        print(f"[db.ai] update_generation_job error: {e}")
        return False


def delete_old_generation_jobs(older_than_hours: int = 48) -> int:
    """TTL sweep — generation jobs have no natural end-of-life action (unlike
    e.g. an exam attempt), so without this the table/temp-PDF references would
    grow forever. Called opportunistically at the start of a new generation
    request rather than via a separate scheduler."""
    try:
        rows = execute_returning(
            "DELETE FROM ai_generation_jobs WHERE created_at < (now() AT TIME ZONE 'utc') - (%s || ' hours')::interval RETURNING id",
            (older_than_hours,),
        )
        return len(rows)
    except Exception as e:
        print(f"[db.ai] delete_old_generation_jobs error: {e}")
        return 0


def get_generation_job(job_id: str) -> Optional[Dict]:
    try:
        return fetch_one("SELECT * FROM ai_generation_jobs WHERE id=%s", (job_id,))
    except Exception as e:
        print(f"[db.ai] get_generation_job error: {e}")
        return None
