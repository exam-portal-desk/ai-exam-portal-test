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

import base64
import json
import threading
import time
import re
import uuid

from app.utils.datetime_service import now_utc_naive
import app.db.discussion as discussion_db
from app.db.users import get_users_by_ids
from app.services.image_storage_service import profile_photo_url_from_key

RATE_LIMIT_SECONDS = 10
MAX_MSG_LEN = 500

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 50
DEFAULT_REPLY_LIMIT = 10
MAX_REPLY_LIMIT = 30

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


def _encode_cursor(row: dict) -> str:
    raw = json.dumps([row['created_at'], row['id']]).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor):
    if not cursor:
        return None, None
    try:
        created_at, cid = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return created_at, cid
    except Exception:
        return None, None


def _shape_rows(rows: list, current_uid, reply_counts: dict = None) -> list:
    """Resolve avatars + ghost-account/soft-delete placeholders + is_own for
    a page of rows, batched (one get_users_by_ids call for the whole page,
    no N+1). reply_counts is only passed for top-level pages.

    PROFESSIONAL APPROACH (Reddit/Slack style), same rule as before:
      - Deleted-account rows (user_id = GHOST_USER_ID) show as
        "[Deleted Account] — This user has deleted their account."
      - Soft-deleted-but-has-replies rows (only reachable here because
        app/db/discussion.py's top-level queries keep them for exactly
        this reason) show as "[message deleted]" so the reply thread
        underneath stays reachable instead of being orphaned.
    """
    live_uids = {r.get('user_id') for r in rows
                 if r.get('user_id') and r.get('user_id') != GHOST_USER_ID}
    users_by_id = get_users_by_ids(list(live_uids))

    shaped = []
    for r in rows:
        uid = r.get('user_id')
        deleted = r.get('is_deleted', False)
        is_ghost = (uid == GHOST_USER_ID)

        if is_ghost:
            r['username'] = DELETED_ACCOUNT_DISPLAY
            r['message'] = DELETED_ACCOUNT_MESSAGE
            r['is_deleted_account'] = True
            r['is_own'] = False
            r['avatar_url'] = None
        elif deleted:
            r['message'] = '[message deleted]'
            r['is_deleted_account'] = False
            r['is_own'] = False
            r['avatar_url'] = None
        else:
            r['is_deleted_account'] = False
            r['is_own'] = (uid == current_uid)
            photo_key = users_by_id.get(str(uid), {}).get('profile_photo_key')
            r['avatar_url'] = profile_photo_url_from_key(photo_key)

        if reply_counts is not None:
            r['reply_count'] = reply_counts.get(r['id'], 0)

        r.pop('user_id', None)
        r.pop('is_deleted', None)
        shaped.append(r)
    return shaped


def get_comments_page(question_id: int, current_uid, cursor: str = None, limit: int = DEFAULT_PAGE_LIMIT) -> dict:
    """Paginated top-level comments (keyset, oldest first). Pinned comments
    are always included in full on the first page only (cursor is None) —
    they're inherently few, so re-sending them on every later page would
    just be wasted payload."""
    limit = max(1, min(limit, MAX_PAGE_LIMIT))
    after_created_at, after_id = _decode_cursor(cursor)

    pinned_rows = discussion_db.get_pinned_top_level(question_id) if not cursor else []
    page_rows = discussion_db.get_top_level_page(question_id, after_created_at, after_id, limit + 1)

    has_more = len(page_rows) > limit
    page_rows = page_rows[:limit]
    next_cursor = _encode_cursor(page_rows[-1]) if has_more and page_rows else None

    all_rows = pinned_rows + page_rows
    counts = discussion_db.get_reply_counts([r['id'] for r in all_rows])
    comments = _shape_rows(all_rows, current_uid, reply_counts=counts)

    return {'comments': comments, 'next_cursor': next_cursor, 'count': get_count(question_id)}


def get_replies_page(question_id: int, parent_id: int, current_uid, cursor: str = None, limit: int = DEFAULT_REPLY_LIMIT) -> dict:
    """Paginated replies for one top-level comment (keyset, oldest first —
    natural conversation order, important since replies may @mention
    earlier replies)."""
    limit = max(1, min(limit, MAX_REPLY_LIMIT))
    after_created_at, after_id = _decode_cursor(cursor)

    rows = discussion_db.get_replies_page(question_id, parent_id, after_created_at, after_id, limit + 1)
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _encode_cursor(rows[-1]) if has_more and rows else None

    return {'replies': _shape_rows(rows, current_uid), 'next_cursor': next_cursor}


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


def create_comment(question_id: int, uid, username: str, data: dict, avatar_url: str = None):
    """
    Validate + persist a new comment. Returns (error_message, status_code, None)
    on failure, or (None, None, payload) on success, where payload is the
    comment shape used for both the HTTP response and the socket broadcast.

    A reply's parent_id is always rewritten to its thread ROOT server-side
    (even if the client sent a reply's own id as parent_id, i.e. "reply to
    a reply") — this keeps the flat 2-tier structure (top-level comments +
    one flat replies bucket each) an actual invariant, not just a UI
    convention, so pagination/reply-count queries never have to recurse.
    "Who this reply is actually addressing" is preserved instead as the
    leading @username the client prefills into the message text — no new
    column needed, and it survives reloads since it's part of `message`.
    """
    msg = (data.get('message') or '').strip()
    if not msg:
        return 'Message is empty', 400, None
    if len(msg) > MAX_MSG_LEN:
        return f'Max {MAX_MSG_LEN} characters allowed', 400, None
    msg = _sanitize(msg)

    parent_id = data.get('parent_id')
    if parent_id:
        parent = discussion_db.get_comment_parent_info(parent_id)
        if parent and parent.get('parent_id'):
            parent_id = parent['parent_id']

    now_iso = now_utc_naive().isoformat()
    temp_id = str(uuid.uuid4())
    record = {
        'question_id': question_id,
        'exam_id': data.get('exam_id'),
        'user_id': uid,
        'username': username,
        'message': msg,
        'parent_id': parent_id,
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
        'avatar_url': avatar_url,
        'message': msg,
        'parent_id': parent_id,
        'created_at': now_iso,
        'is_pinned': False,
        'is_best_answer': False,
        'is_edited': False,
        'is_deleted_account': False,
        'reply_count': 0,
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
