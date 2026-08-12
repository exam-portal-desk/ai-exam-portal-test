"""
app/services/user_deletion_service.py  —  v3.0 (Account & Data Deletion Policy)

Implements the platform's Account & Data Deletion Policy:

  PRIVATE DATA (owned exclusively by the account, of no value to anyone
  else) is permanently deleted: profile, credentials, sessions/tokens,
  exam attempts/results/responses, private AI conversations, private
  notebooks (+ their pages/objects/assets/storage files), private chat.

  PUBLIC / SHARED CONTENT that other users depend on (published Public
  Notebooks, question-discussion comments) is PRESERVED — never deleted
  just because its author's account is gone — but is no longer linked to
  a live account:
    - Public notebooks: owner_id is reassigned to a permanent Ghost User
      row (id=-1) so the DB's ON DELETE CASCADE from notes_notebooks.owner_id
      never fires for them, author_deleted is set true, and the existing
      author_display_name snapshot (already captured at publish time) is
      left untouched so the notebook keeps crediting its real author, with
      the frontend appending "(Deleted User)" wherever author_deleted=true.
    - Discussion comments: user_id is reassigned to the Ghost User; the
      original username/message text is left untouched in the database
      (the contribution itself is preserved), and discussion.py's read path
      already renders any Ghost-authored row as "Deleted Account" — it
      does not need — and must not require — the real users row to exist.

  Nothing here ever falls back to an exposed numeric label like "User 4":
  once an account is deleted, its private exam/AI/notebook data simply no
  longer exists, and its preserved public contributions are keyed off the
  Ghost User sentinel or a display-name snapshot, never a dangling id.

DELETION ORDER:
  Step 0  Ensure the permanent Ghost User row exists (own commit — must
          exist before anything below can point an ON DELETE CASCADE
          foreign key, e.g. notes_notebooks.owner_id, at it)
  ── everything below runs in ONE transaction — all-or-nothing ──
  Step 1  Reassign question_discussions.user_id → Ghost (content preserved)
  Step 2  Handle conversations (DM purge / group transfer)
  Step 3  Hard-delete remaining chat_messages (sender_id NOT NULL)
  Step 4  Delete string-keyed records (pw_tokens, login_attempts)
  Step 5  Anonymize requests_raised (keep audit trail, scrub PII)
  Step 6  Delete private exam data (responses, results, exam_attempts)
  Step 7  Delete private AI data (chat history, explanations, usage)
  Step 8  Notebooks: hard-delete private/trashed, reassign+preserve public
  Step 9  Delete sessions, refresh tokens, chat connections
  Step 10 DELETE users row
"""

from __future__ import annotations
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
from app.db import fetch_one, fetch_all, execute, insert_returning, transaction

logger = logging.getLogger(__name__)

GHOST_USER_ID      = -1
GHOST_USERNAME     = "deleted_user"
GHOST_DISPLAY_NAME = "[Deleted User]"
GHOST_EMAIL        = "ghost@system.internal"


# ─── Public entry point ────────────────────────────────────────────────────

def delete_user_completely(user_id: int) -> Tuple[bool, str]:
    """Delete a user and ALL private data; preserve+anonymize public
    contributions. Returns (success, message). Never raises."""
    try:
        uid = int(user_id)

        meta = _fetch_user_meta(uid)
        if meta is None:
            return True, "Account already deleted"

        email    = str(meta.get("email", "")).strip().lower()
        username = str(meta.get("username", "")).strip()

        logger.info("[user_deletion] BEGIN uid=%s username=%s", uid, username)

        _ensure_ghost_user()  # Step 0 — own commit, must precede any FK reassignment to it

        # Best-effort storage cleanup for notebooks that will be hard-deleted.
        # Not transactional with the DB (external storage provider) — same
        # ordering already used by notes_service.permanently_delete_notebook:
        # delete the files first, then the rows, so a failure here never
        # leaves a DB row pointing at bytes we already destroyed.
        private_notebook_ids, public_notebook_ids = _classify_and_purge_notebook_storage(uid)

        with transaction() as cur:
            _reassign_discussions(cur, uid)                                   # Step 1
            _handle_conversations(cur, uid)                                   # Step 2
            _delete_chat_messages(cur, uid)                                   # Step 3
            _delete_string_keyed_records(cur, email, username)                # Steps 4+5
            _delete_exam_data(cur, uid)                                       # Step 6
            _delete_ai_data(cur, uid)                                         # Step 7
            _purge_and_reassign_notebooks(cur, private_notebook_ids, public_notebook_ids)  # Step 8
            _delete_sessions_and_connections(cur, uid)                        # Step 9
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))              # Step 10

        logger.info(
            "[user_deletion] COMPLETE uid=%s notebooks_purged=%s notebooks_preserved=%s",
            uid, len(private_notebook_ids), len(public_notebook_ids),
        )
        return True, "Account deleted successfully"

    except Exception as exc:
        import traceback
        logger.error("[user_deletion] FATAL uid=%s: %s\n%s",
                     user_id, exc, traceback.format_exc())
        return False, f"Deletion failed: {exc}"


# ─── Step 0 — Ghost user ──────────────────────────────────────────────────

def _ensure_ghost_user() -> None:
    """Insert the permanent ghost/tombstone user if not present. Safe to
    call repeatedly. This row is never a real login — its sole purpose is
    to be a valid FK target for preserved public content whose real author
    account has been deleted."""
    try:
        if fetch_one("SELECT id FROM users WHERE id=%s", (GHOST_USER_ID,)):
            return
        insert_returning("users", {
            "id":        GHOST_USER_ID,
            "username":  GHOST_USERNAME,
            "email":     GHOST_EMAIL,
            "password":  "GHOST_ACCOUNT_NO_LOGIN_POSSIBLE",
            "full_name": GHOST_DISPLAY_NAME,
            "role":      "ghost",
        })
        logger.info("[user_deletion] Ghost user created id=%s", GHOST_USER_ID)
    except Exception as e:
        logger.warning("[user_deletion] _ensure_ghost_user (may already exist): %s", e)


# ─── Step 1 — Reassign discussions (content preserved) ───────────────────

def _reassign_discussions(cur, uid: int) -> None:
    """Public discussion contributions are preserved, not deleted: only the
    account link changes. The original username/message text is left
    exactly as posted — discussion.py's read path already renders any row
    with user_id=GHOST_USER_ID as "Deleted Account" regardless of what's
    stored, so overwriting the text here would only destroy content for no
    display benefit (and would violate "preserve the contribution")."""
    try:
        cur.execute("UPDATE question_discussions SET user_id=%s WHERE user_id=%s", (GHOST_USER_ID, uid))
        logger.info("[user_deletion] Reassigned %s discussions uid=%s -> ghost", cur.rowcount, uid)
    except Exception as e:
        logger.error("[user_deletion] _reassign_discussions uid=%s: %s", uid, e)
        raise


# ─── Step 2 — Handle conversations ───────────────────────────────────────

def _handle_conversations(cur, uid: int) -> None:
    try:
        cur.execute("SELECT conversation_id FROM chat_members WHERE user_id=%s", (uid,))
        conv_ids = list({r["conversation_id"] for r in cur.fetchall()})
        if not conv_ids:
            return

        cur.execute("SELECT id, is_group, created_by FROM chat_conversations WHERE id = ANY(%s)", (conv_ids,))
        convs_map = {c["id"]: c for c in cur.fetchall()}

        dm_ids, grp_items = [], []
        for cid in conv_ids:
            conv = convs_map.get(cid)
            if not conv:
                continue
            if conv["is_group"]:
                grp_items.append((cid, conv))
            else:
                dm_ids.append(cid)

        for cid in dm_ids:
            _purge_conversation(cur, cid)

        for cid, conv in grp_items:
            cur.execute(
                "SELECT user_id FROM chat_members WHERE conversation_id=%s AND user_id != %s", (cid, uid)
            )
            others = [m["user_id"] for m in cur.fetchall()]
            _handle_group(cur, uid, cid, conv, others)

        logger.info("[user_deletion] %s DMs purged, %s groups handled uid=%s",
                    len(dm_ids), len(grp_items), uid)
    except Exception as e:
        logger.error("[user_deletion] _handle_conversations uid=%s: %s", uid, e)
        raise


def _handle_group(cur, uid: int, conv_id: int, conv: Dict, others: List[int]) -> None:
    if not others:
        _purge_conversation(cur, conv_id)
        return
    if conv.get("created_by") == uid:
        cur.execute("UPDATE chat_conversations SET created_by=%s WHERE id=%s", (others[0], conv_id))
    cur.execute("DELETE FROM chat_members WHERE conversation_id=%s AND user_id=%s", (conv_id, uid))


def _purge_conversation(cur, conv_id: int) -> None:
    """Delete conversation + all children in FK-safe order."""
    cur.execute("DELETE FROM chat_messages WHERE conversation_id=%s", (conv_id,))
    cur.execute("DELETE FROM chat_members WHERE conversation_id=%s", (conv_id,))
    cur.execute("DELETE FROM chat_unread WHERE conversation_id=%s", (conv_id,))
    cur.execute("DELETE FROM chat_visibility WHERE conversation_id=%s", (conv_id,))
    cur.execute("DELETE FROM chat_conversations WHERE id=%s", (conv_id,))


# ─── Step 3 — Hard-delete remaining chat messages ────────────────────────

def _delete_chat_messages(cur, uid: int) -> None:
    """Remaining messages (mainly from groups; DM messages already deleted
    in Step 2). sender_id is NOT NULL — no other option than hard delete,
    and these are private conversation content, not public contributions."""
    try:
        cur.execute("DELETE FROM chat_messages WHERE sender_id=%s", (uid,))
        logger.info("[user_deletion] Deleted %s chat_messages uid=%s", cur.rowcount, uid)
    except Exception as e:
        logger.error("[user_deletion] _delete_chat_messages uid=%s: %s", uid, e)
        raise


# ─── Steps 4+5 — String-keyed records ────────────────────────────────────

def _delete_string_keyed_records(cur, email: str, username: str) -> None:
    try:
        if email:
            cur.execute("DELETE FROM pw_tokens WHERE email=%s", (email,))
        if username:
            cur.execute("DELETE FROM login_attempts WHERE identifier=%s", (username,))
        if email:
            cur.execute("DELETE FROM login_attempts WHERE identifier=%s", (email,))
        _anonymize_requests(cur, username, email)
    except Exception as e:
        logger.error("[user_deletion] _delete_string_keyed_records: %s", e)
        raise


def _anonymize_requests(cur, username: str, email: str) -> None:
    """requests_raised is a support/admin audit trail, not user-facing
    public content — kept (per existing product behavior) but scrubbed of
    PII, same pattern as everywhere else preserved content is anonymized."""
    try:
        conditions, params = [], []
        if username:
            conditions.append("username=%s"); params.append(username)
        if email:
            conditions.append("email=%s"); params.append(email)
        if not conditions:
            return
        where_sql = " OR ".join(conditions)
        now = datetime.now(timezone.utc).isoformat()
        cur.execute(
            f"UPDATE requests_raised SET request_status=%s, processed_date=%s, processed_by=%s "
            f"WHERE ({where_sql}) AND request_status=%s",
            ["withdrawn", now, "system:account_deleted"] + params + ["pending"],
        )
        cur.execute(
            f"UPDATE requests_raised SET username=%s, email=%s, reason=%s WHERE ({where_sql})",
            ["[deleted]", "[deleted]", "[Content removed — account deleted by user]"] + params,
        )
    except Exception as e:
        logger.error("[user_deletion] _anonymize_requests: %s", e)
        raise


# ─── Step 6 — Private exam data ──────────────────────────────────────────

def _delete_exam_data(cur, uid: int) -> None:
    """Exam attempts/results/responses belong exclusively to the student who
    took the exam — no one else can see them — so they are fully removed.
    This is the fix for the "User 4" orphaned-result bug: going forward, a
    result row can never outlive the account that owns it."""
    try:
        cur.execute("SELECT id FROM results WHERE student_id=%s", (uid,))
        result_ids = [r["id"] for r in cur.fetchall()]
        if result_ids:
            cur.execute("DELETE FROM responses WHERE result_id = ANY(%s)", (result_ids,))
        cur.execute("DELETE FROM results WHERE student_id=%s", (uid,))
        results_deleted = cur.rowcount
        cur.execute("DELETE FROM exam_attempts WHERE student_id=%s", (uid,))
        logger.info(
            "[user_deletion] Deleted %s results (%s responses), %s exam_attempts uid=%s",
            results_deleted, len(result_ids), cur.rowcount, uid,
        )
    except Exception as e:
        logger.error("[user_deletion] _delete_exam_data uid=%s: %s", uid, e)
        raise


# ─── Step 7 — Private AI data ─────────────────────────────────────────────

def _delete_ai_data(cur, uid: int) -> None:
    """AI chat/assistant conversations and explanation history are private
    to the user — never shown to anyone else — so they are fully removed."""
    try:
        for table in ("ai_chat_history", "ai_explanation_history", "ai_explanation_usage", "ai_usage_tracking"):
            cur.execute(f"DELETE FROM {table} WHERE user_id=%s", (uid,))
        logger.info("[user_deletion] Deleted AI chat/explanation/usage data uid=%s", uid)
    except Exception as e:
        logger.error("[user_deletion] _delete_ai_data uid=%s: %s", uid, e)
        raise


# ─── Step 8 — Notebooks: purge private, preserve+anonymize public ───────

def _classify_and_purge_notebook_storage(uid: int) -> Tuple[List[str], List[str]]:
    """Read-only classification + best-effort storage cleanup (runs before
    the DB transaction — see module docstring). Returns (private_ids,
    public_ids): private_ids covers private notebooks AND anything already
    in the user's own Trash (soft-deleted) — both are purged outright, since
    neither was ever visible to anyone but the owner. public_ids covers
    only currently-published Public notebooks, which are preserved."""
    from app.db import notes as notes_db
    from app.services import notes_storage_service

    notebooks = fetch_all("SELECT id, visibility, deleted_at FROM notes_notebooks WHERE owner_id=%s", (uid,))
    private_ids, public_ids = [], []
    for nb in notebooks:
        if nb.get("visibility") == "public" and not nb.get("deleted_at"):
            public_ids.append(nb["id"])
        else:
            private_ids.append(nb["id"])

    for notebook_id in private_ids:
        try:
            notes_storage_service.delete_assets(notes_db.list_notebook_assets(notebook_id))
        except Exception as e:
            # Best-effort: an orphaned storage file is a minor cleanup issue,
            # never a reason to abort account deletion.
            logger.warning("[user_deletion] storage cleanup notebook=%s: %s", notebook_id, e)

    return private_ids, public_ids


def _purge_and_reassign_notebooks(cur, private_ids: List[str], public_ids: List[str]) -> None:
    if private_ids:
        cur.execute("SELECT id FROM notes_pages WHERE notebook_id = ANY(%s::uuid[])", (private_ids,))
        page_ids = [r["id"] for r in cur.fetchall()]
        if page_ids:
            cur.execute("DELETE FROM notes_objects WHERE page_id = ANY(%s::uuid[])", (page_ids,))
        cur.execute("DELETE FROM notes_assets WHERE notebook_id = ANY(%s::uuid[])", (private_ids,))
        cur.execute("DELETE FROM notes_pages WHERE notebook_id = ANY(%s::uuid[])", (private_ids,))
        cur.execute("DELETE FROM notes_notebooks WHERE id = ANY(%s::uuid[])", (private_ids,))

    if public_ids:
        # Reassign (not delete) — the notebook, its pages/objects/assets and
        # educational content remain fully intact. author_display_name was
        # already captured as a snapshot when the notebook was published, so
        # the notebook keeps crediting its real author; author_deleted=true
        # lets every consumer append "(Deleted User)" without needing a live
        # users row. notes_assets.owner_id must move too, or it would be
        # cascade-deleted out from under the (now-preserved) notebook the
        # moment the real user row is removed below.
        cur.execute(
            "UPDATE notes_notebooks SET owner_id=%s, author_deleted=TRUE WHERE id = ANY(%s::uuid[])",
            (GHOST_USER_ID, public_ids),
        )
        cur.execute(
            "UPDATE notes_assets SET owner_id=%s WHERE notebook_id = ANY(%s::uuid[])",
            (GHOST_USER_ID, public_ids),
        )


# ─── Step 9 — Sessions, tokens, connections ──────────────────────────────

def _delete_sessions_and_connections(cur, uid: int) -> None:
    """Kills active sessions/refresh tokens (immediate logout everywhere)
    and the user's connection requests — all private to the account."""
    try:
        cur.execute("DELETE FROM sessions WHERE user_id=%s", (uid,))
        cur.execute("DELETE FROM jwt_refresh_tokens WHERE user_id=%s", (uid,))
        cur.execute("DELETE FROM chat_connections WHERE requester_id=%s OR recipient_id=%s", (uid, uid))
    except Exception as e:
        logger.error("[user_deletion] _delete_sessions_and_connections uid=%s: %s", uid, e)
        raise


# ─── Utility ──────────────────────────────────────────────────────────────

def _fetch_user_meta(uid: int) -> Optional[Dict]:
    try:
        return fetch_one("SELECT id, email, username FROM users WHERE id=%s", (uid,))
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
        row = fetch_one("SELECT id, username, role FROM users WHERE id=%s", (GHOST_USER_ID,))
        if not row:
            return False, f"Ghost user id={GHOST_USER_ID} could not be verified"
        return True, f"Ghost user ready: id={row['id']} username={row['username']}"
    except Exception as e:
        return False, f"Ghost user setup failed: {e}"
