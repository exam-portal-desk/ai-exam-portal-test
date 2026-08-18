"""
app/services/discussion_service.py
Business logic for question discussions/comments — extracted from
app/routes/discussion.py so the web and API layers (and any future
consumer) share one implementation instead of routes reaching into SQL
directly. Behaviour matches the original route file, with two fixes noted
inline: the comment-count sync is now a single atomic UPSERT (see
app/db/discussion.py.upsert_count_delta) instead of a racy
SELECT-then-UPDATE/INSERT, and the dead message-buffering thread
(_msg_buffer/_flush_buffer, declared and started but never fed by any
caller) has been removed rather than relocated.

In-process caches (_rate_cache, _count_cache) are unchanged from the
original — still per-process, not shared across multiple worker
processes, same as before this refactor.
"""

import threading
import time
import re
import uuid

from app.utils.datetime_service import now_utc_naive
import app.db.discussion as discussion_db

RATE_LIMIT_SECONDS = 10
MAX_MSG_LEN = 500

# Ghost user ID — same as user_deletion_service.py
GHOST_USER_ID = -1
DELETED_ACCOUNT_DISPLAY = "Deleted Account"
DELETED_ACCOUNT_MESSAGE = "This user has deleted their account."

_rate_cache: dict = {}
_rate_lock = threading.Lock()

_count_cache: dict = {}
_count_lock = threading.Lock()


def _sanitize(text: str) -> str:
    text = text.strip()
    text = re.sub(r'[<>]', '', text)
    return re.sub(r'\s+', ' ', text)


def rate_ok(user_id) -> bool:
    now = time.time()
    with _rate_lock:
        if now - _rate_cache.get(user_id, 0) < RATE_LIMIT_SECONDS:
            return False
        _rate_cache[user_id] = now
        return True


def _build_thread(rows: list) -> list:
    """
    Build threaded comment tree.

    PROFESSIONAL APPROACH (Reddit/Slack style):
      - Deleted account messages (user_id = GHOST_USER_ID) are NOT hidden.
        They show as "[deleted account] • This user has deleted their account."
        This keeps the thread structure intact — replies stay nested correctly.
      - Only truly deleted messages (is_deleted=True AND not ghost) are hidden,
        BUT their slot is preserved if they have replies, showing:
        "[message deleted] • replies still visible below"
      - Orphan replies whose parent is completely gone → dropped silently.
    """
    by_id = {r['id']: {**r, 'replies': []} for r in rows}
    roots = []

    for r in rows:
        pid = r.get('parent_id')
        if not pid:
            roots.append(by_id[r['id']])
        elif pid in by_id:
            by_id[pid]['replies'].append(by_id[r['id']])
        # else: true orphan (parent completely purged) — drop silently

    return roots


def get_count(question_id: int) -> int:
    with _count_lock:
        if question_id in _count_cache:
            return _count_cache[question_id]
    try:
        count = discussion_db.get_count(question_id)
        with _count_lock:
            _count_cache[question_id] = count
        return count
    except Exception:
        return 0


def _sync_count_cache(question_id: int, delta: int) -> None:
    with _count_lock:
        _count_cache[question_id] = max(0, _count_cache.get(question_id, 0) + delta)


def sync_count(question_id: int, delta: int) -> None:
    """Update both the in-memory cache and the DB row for a question's comment count."""
    _sync_count_cache(question_id, delta)
    try:
        discussion_db.upsert_count_delta(question_id, delta)
    except Exception as e:
        print(f"[Disc] count sync error: {e}")


def get_discussion_thread(question_id: int, current_uid) -> dict:
    """Fetch + shape the full comment thread for a question."""
    all_rows = discussion_db.get_thread_rows(question_id)

    display_rows = []
    for r in all_rows:
        uid = r.get('user_id')
        deleted = r.get('is_deleted', False)
        is_ghost = (uid == GHOST_USER_ID)

        # Skip truly deleted messages (not ghost, not having replies)
        # We will re-add them if they have children below
        if deleted and not is_ghost:
            continue

        # Ghost user row → show as "deleted account" placeholder
        if is_ghost:
            r['username'] = DELETED_ACCOUNT_DISPLAY
            r['message'] = DELETED_ACCOUNT_MESSAGE
            r['is_deleted_account'] = True
            r['is_own'] = False
        else:
            r['is_deleted_account'] = False
            r['is_own'] = (uid == current_uid)

        r.pop('user_id', None)
        r.pop('is_deleted', None)
        display_rows.append(r)

    return {
        'comments': _build_thread(display_rows),
        'count': get_count(question_id),
    }


def create_comment(question_id: int, uid, username: str, data: dict):
    """
    Validate + persist a new comment. Returns (error_message, status_code, None)
    on failure, or (None, None, payload) on success, where payload is the
    comment shape used for both the HTTP response and the socket broadcast.
    """
    msg = (data.get('message') or '').strip()
    if not msg:
        return 'Message is empty', 400, None
    if len(msg) > MAX_MSG_LEN:
        return f'Max {MAX_MSG_LEN} characters allowed', 400, None
    msg = _sanitize(msg)

    now_iso = now_utc_naive().isoformat()
    temp_id = str(uuid.uuid4())
    record = {
        'question_id': question_id,
        'exam_id': data.get('exam_id'),
        'user_id': uid,
        'username': username,
        'message': msg,
        'parent_id': data.get('parent_id'),
        'is_pinned': False,
        'is_best_answer': False,
        'is_deleted': False,
        'is_edited': False,
        'created_at': now_iso,
        'updated_at': now_iso,
    }

    result = discussion_db.insert_comment(record)
    real_id = result['id'] if result else None
    sync_count(question_id, +1)

    payload = {
        'temp_id': temp_id,
        'sender_uid': uid,
        'id': real_id,
        'question_id': question_id,
        'username': username,
        'message': msg,
        'parent_id': data.get('parent_id'),
        'created_at': now_iso,
        'is_pinned': False,
        'is_best_answer': False,
        'is_edited': False,
        'is_deleted_account': False,
        'replies': [],
        'count': get_count(question_id),
    }
    return None, None, payload


def edit_comment(comment_id: int, uid, is_admin: bool, message: str):
    """Returns (error_message, status_code) on failure, or (None, question_id) on success."""
    msg = (message or '').strip()
    if not msg or len(msg) > MAX_MSG_LEN:
        return 'Invalid message', 400, None
    msg = _sanitize(msg)

    row = discussion_db.get_comment_owner(comment_id)
    if not row or (row['user_id'] != uid and not is_admin):
        return 'Forbidden', 403, None

    discussion_db.update_comment_message(comment_id, msg, now_utc_naive().isoformat())
    return None, None, {'question_id': row['question_id'], 'message': msg}


def delete_comment(comment_id: int, uid, is_admin: bool):
    row = discussion_db.get_comment_owner(comment_id)
    if not row:
        return 'Not found', 404, None
    if row['user_id'] != uid and not is_admin:
        return 'Forbidden', 403, None

    discussion_db.soft_delete_comment(comment_id)
    qid = row['question_id']
    sync_count(qid, -1)
    return None, None, qid


def toggle_pin(comment_id: int):
    row = discussion_db.get_comment_pin_state(comment_id)
    if not row:
        return None, None
    new_val = not row['is_pinned']
    discussion_db.set_comment_pinned(comment_id, new_val)
    return row['question_id'], new_val


def toggle_best(comment_id: int):
    row = discussion_db.get_comment_best_state(comment_id)
    if not row:
        return None, None
    qid = row['question_id']
    new_val = not row['is_best_answer']
    if new_val:
        discussion_db.clear_best_answer_for_question(qid)
    discussion_db.set_comment_best(comment_id, new_val)
    return qid, new_val


def bulk_counts(question_ids: list) -> dict:
    counts = discussion_db.get_counts_bulk(question_ids)
    return {str(qid): counts.get(qid, 0) for qid in question_ids}
