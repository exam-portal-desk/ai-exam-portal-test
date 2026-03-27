"""
app/services/user_deletion_service.py  —  v2.0 (COMPLETE REWRITE)

FIX: Postgres error 23502 — null value in column "user_id" of relation
     "question_discussions" violates not-null constraint

ROOT CAUSE (v1 bug):
  _anonymize_discussions() updated username/message/is_deleted but then
  relied on Postgres to SET NULL on question_discussions.user_id when the
  users row was deleted. But user_id is NOT NULL — SET NULL is impossible.

FIX — Ghost/Tombstone User pattern:
  Reassign question_discussions.user_id → GHOST_USER_ID BEFORE deleting
  the real user row. Satisfies NOT NULL + FK in one UPDATE. When user row
  is deleted, zero FK references point to uid in that table.

DELETION ORDER (FK-safe):
  Step 0  Ensure ghost user exists
  Step 1  Reassign + anonymize question_discussions     ← THE FIX
  Step 2  Handle conversations (DM purge / group xfer)
  Step 3  Hard-delete chat_messages (sender_id NOT NULL)
  Step 4  Delete string-keyed records (pw_tokens, login_attempts)
  Step 5  Anonymize requests_raised (keep audit, scrub PII)
  Step 6  DELETE users row → Postgres CASCADE handles the rest
"""

from __future__ import annotations
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
from app.db import supabase

logger = logging.getLogger(__name__)

GHOST_USER_ID      = -1
GHOST_USERNAME     = "deleted_user"
GHOST_DISPLAY_NAME = "[Deleted User]"
GHOST_EMAIL        = "ghost@system.internal"
ANON_USERNAME      = "[deleted user]"
ANON_MESSAGE       = "[This message has been removed]"


# ─── Public entry point ────────────────────────────────────────────────────

def delete_user_completely(user_id: int) -> Tuple[bool, str]:
    """Delete a user and ALL related data. Returns (success, message). Never raises."""
    try:
        uid = int(user_id)

        meta = _fetch_user_meta(uid)
        if meta is None:
            return True, "Account already deleted"

        email    = str(meta.get("email", "")).strip().lower()
        username = str(meta.get("username", "")).strip()

        logger.info("[user_deletion] BEGIN uid=%s username=%s", uid, username)

        _ensure_ghost_user()                          # Step 0
        _reassign_discussions(uid)                    # Step 1  ← THE FIX
        _handle_conversations(uid)                    # Step 2
        _delete_chat_messages(uid)                    # Step 3
        _delete_string_keyed_records(email, username) # Steps 4+5

        # Step 6 — DELETE triggers CASCADE on: ai_chat_history,
        # ai_usage_tracking, exam_attempts, results, responses,
        # chat_connections, chat_members, chat_unread,
        # chat_visibility, sessions
        supabase.table("users").delete().eq("id", uid).execute()

        logger.info("[user_deletion] COMPLETE uid=%s", uid)
        return True, "Account deleted successfully"

    except Exception as exc:
        import traceback
        logger.error("[user_deletion] FATAL uid=%s: %s\n%s",
                     user_id, exc, traceback.format_exc())
        return False, f"Deletion failed: {exc}"


# ─── Step 0 — Ghost user ──────────────────────────────────────────────────

def _ensure_ghost_user() -> None:
    """Insert permanent ghost user if not exists. Safe to call repeatedly."""
    try:
        res = supabase.table("users").select("id").eq("id", GHOST_USER_ID).execute()
        if res.data:
            return
        supabase.table("users").insert({
            "id":        GHOST_USER_ID,
            "username":  GHOST_USERNAME,
            "email":     GHOST_EMAIL,
            "password":  "GHOST_ACCOUNT_NO_LOGIN_POSSIBLE",
            "full_name": GHOST_DISPLAY_NAME,
            "role":      "ghost",
        }).execute()
        logger.info("[user_deletion] Ghost user created id=%s", GHOST_USER_ID)
    except Exception as e:
        logger.warning("[user_deletion] _ensure_ghost_user (may already exist): %s", e)


# ─── Step 1 — Reassign discussions  ← THE FIX ─────────────────────────────

def _reassign_discussions(uid: int) -> None:
    """
    THE FIX for 23502.

    BROKEN (old): updates username/is_deleted, leaves user_id pointing to uid
                  → Postgres tries SET NULL on NOT NULL column → CRASH

    FIXED (new):  sets user_id = GHOST_USER_ID in the same UPDATE
                  → satisfies NOT NULL + FK, zero references left to uid
                  → user row deletion has nothing to cascade/nullify here
    """
    try:
        result = (
            supabase.table("question_discussions")
            .update({
                "user_id":    GHOST_USER_ID,      # ← THIS IS THE FIX
                "username":   ANON_USERNAME,
                "message":    ANON_MESSAGE,
                "is_deleted": True,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("user_id", uid)
            .execute()
        )
        count = len(result.data) if result.data else 0
        logger.info("[user_deletion] Reassigned %s discussions uid=%s → ghost", count, uid)
    except Exception as e:
        logger.error("[user_deletion] _reassign_discussions uid=%s: %s", uid, e)
        raise


# ─── Step 2 — Handle conversations ───────────────────────────────────────

def _handle_conversations(uid: int) -> None:
    try:
        members_res = (
            supabase.table("chat_members")
            .select("conversation_id")
            .eq("user_id", uid)
            .execute()
        )
        conv_ids = list({r["conversation_id"] for r in (members_res.data or [])})
        if not conv_ids:
            return

        convs_res = (
            supabase.table("chat_conversations")
            .select("id, is_group, created_by")
            .in_("id", conv_ids)
            .execute()
        )
        convs_map = {c["id"]: c for c in (convs_res.data or [])}

        dm_ids = []
        grp_items = []

        for cid in conv_ids:
            conv = convs_map.get(cid)
            if not conv:
                continue
            if conv["is_group"]:
                grp_items.append((cid, conv))
            else:
                dm_ids.append(cid)

        for cid in dm_ids:
            _purge_conversation(cid)

        for cid, conv in grp_items:
            others_res = (
                supabase.table("chat_members")
                .select("user_id")
                .eq("conversation_id", cid)
                .neq("user_id", uid)
                .execute()
            )
            others = [m["user_id"] for m in (others_res.data or [])]
            _handle_group(uid, cid, conv, others)

        logger.info("[user_deletion] %s DMs purged, %s groups handled uid=%s",
                    len(dm_ids), len(grp_items), uid)
    except Exception as e:
        logger.error("[user_deletion] _handle_conversations uid=%s: %s", uid, e)
        raise


def _handle_group(uid: int, conv_id: int, conv: Dict, others: List[int]) -> None:
    if not others:
        _purge_conversation(conv_id)
        return
    if conv.get("created_by") == uid:
        supabase.table("chat_conversations").update(
            {"created_by": others[0]}
        ).eq("id", conv_id).execute()


def _purge_conversation(conv_id: int) -> None:
    """Delete conversation + all children in FK-safe order."""
    try:
        supabase.table("chat_messages").delete().eq("conversation_id", conv_id).execute()
        supabase.table("chat_members").delete().eq("conversation_id", conv_id).execute()
        supabase.table("chat_unread").delete().eq("conversation_id", conv_id).execute()
        supabase.table("chat_visibility").delete().eq("conversation_id", conv_id).execute()
        supabase.table("chat_conversations").delete().eq("id", conv_id).execute()
    except Exception as e:
        logger.error("[user_deletion] _purge_conversation cid=%s: %s", conv_id, e)
        raise


# ─── Step 3 — Hard-delete chat messages ──────────────────────────────────

def _delete_chat_messages(uid: int) -> None:
    """
    Bulk-delete remaining messages (mainly from groups; DM messages
    already deleted in Step 2). sender_id is NOT NULL — no other option.
    """
    try:
        result = (
            supabase.table("chat_messages")
            .delete()
            .eq("sender_id", uid)
            .execute()
        )
        count = len(result.data) if result.data else 0
        logger.info("[user_deletion] Deleted %s chat_messages uid=%s", count, uid)
    except Exception as e:
        logger.error("[user_deletion] _delete_chat_messages uid=%s: %s", uid, e)
        raise


# ─── Steps 4+5 — String-keyed records ────────────────────────────────────

def _delete_string_keyed_records(email: str, username: str) -> None:
    try:
        if email:
            supabase.table("pw_tokens").delete().eq("email", email).execute()
        if username:
            supabase.table("login_attempts").delete().eq("identifier", username).execute()
        if email:
            supabase.table("login_attempts").delete().eq("identifier", email).execute()
        _anonymize_requests(username, email)
    except Exception as e:
        logger.error("[user_deletion] _delete_string_keyed_records: %s", e)
        raise


def _anonymize_requests(username: str, email: str) -> None:
    try:
        conditions = []
        if username:
            conditions.append(f"username.eq.{username}")
        if email:
            conditions.append(f"email.eq.{email}")
        if not conditions:
            return
        f = ",".join(conditions)
        now = datetime.now(timezone.utc).isoformat()
        supabase.table("requests_raised").update({
            "request_status": "withdrawn",
            "processed_date": now,
            "processed_by":   "system:account_deleted",
        }).or_(f).eq("request_status", "pending").execute()
        supabase.table("requests_raised").update({
            "username": "[deleted]",
            "email":    "[deleted]",
            "reason":   "[Content removed — account deleted by user]",
        }).or_(f).execute()
    except Exception as e:
        logger.error("[user_deletion] _anonymize_requests: %s", e)
        raise


# ─── Utility ──────────────────────────────────────────────────────────────

def _fetch_user_meta(uid: int) -> Optional[Dict]:
    try:
        res = (
            supabase.table("users")
            .select("id, email, username")
            .eq("id", uid)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error("[user_deletion] _fetch_user_meta uid=%s: %s", uid, e)
        return None


# ─── One-time setup ───────────────────────────────────────────────────────

def setup_ghost_user_migration() -> Tuple[bool, str]:
    """
    Run once before first deployment:
        from app.services.user_deletion_service import setup_ghost_user_migration
        print(setup_ghost_user_migration())
    """
    try:
        _ensure_ghost_user()
        check = (
            supabase.table("users")
            .select("id, username, role")
            .eq("id", GHOST_USER_ID)
            .execute()
        )
        if not check.data:
            return False, f"Ghost user id={GHOST_USER_ID} could not be verified"
        row = check.data[0]
        return True, f"Ghost user ready: id={row['id']} username={row['username']}"
    except Exception as e:
        return False, f"Ghost user setup failed: {e}"