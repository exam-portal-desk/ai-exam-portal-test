"""
app/db/discussion.py
Data access for question discussions/comments — extracted from
app/routes/discussion.py, which previously called the low-level
fetch_one/fetch_all/execute/insert_returning primitives directly with
inline SQL. Behaviour is unchanged; this module exists so the discussion
feature has the same routes -> service -> db layering as the rest of the
app, and so app/routes/web/... and app/routes/api/v01/discussions.py can
share one service layer instead of duplicating query logic.
"""

from app.db import fetch_one, fetch_all, execute, insert_returning


_COLS = ('id,question_id,exam_id,user_id,username,message,parent_id,is_pinned,is_best_answer,'
         'is_edited,created_at,updated_at,is_deleted')

# A soft-deleted top-level comment is still included if it has at least one
# non-deleted reply — rendered as a "[message deleted]" placeholder so the
# reply thread underneath it stays reachable instead of being orphaned.
# Same rule the old full-fetch Python logic enforced, moved into SQL now
# that these queries are page-bound (LIMIT-ed) rather than whole-table.
_TOP_LEVEL_DELETE_GUARD = (
    '(is_deleted=false OR EXISTS ('
    'SELECT 1 FROM question_discussions r WHERE r.parent_id=question_discussions.id AND r.is_deleted=false))'
)


def get_pinned_top_level(question_id: int) -> list:
    """Pinned top-level comments — always shown first, on every page load.
    Pinned counts are inherently small, so no pagination needed here."""
    return fetch_all(
        f'SELECT {_COLS} FROM question_discussions '
        f'WHERE question_id=%s AND parent_id IS NULL AND is_pinned=true AND {_TOP_LEVEL_DELETE_GUARD} '
        'ORDER BY created_at ASC, id ASC',
        (question_id,),
    )


def get_top_level_page(question_id: int, after_created_at, after_id, limit: int) -> list:
    """Keyset (cursor) page of non-pinned top-level comments, oldest first."""
    sql = (
        f'SELECT {_COLS} FROM question_discussions '
        f'WHERE question_id=%s AND parent_id IS NULL AND is_pinned=false AND {_TOP_LEVEL_DELETE_GUARD}'
    )
    params = [question_id]
    if after_created_at is not None and after_id is not None:
        sql += ' AND (created_at, id) > (%s, %s)'
        params += [after_created_at, after_id]
    sql += ' ORDER BY created_at ASC, id ASC LIMIT %s'
    params.append(limit)
    return fetch_all(sql, params)


def get_replies_page(question_id: int, parent_id: int, after_created_at, after_id, limit: int) -> list:
    """Keyset page of one comment's direct replies, oldest first. Replies
    are always flat (never themselves a parent), so no delete-guard is
    needed here — a deleted reply has no children to orphan."""
    sql = (
        f'SELECT {_COLS} FROM question_discussions '
        'WHERE question_id=%s AND parent_id=%s AND is_deleted=false'
    )
    params = [question_id, parent_id]
    if after_created_at is not None and after_id is not None:
        sql += ' AND (created_at, id) > (%s, %s)'
        params += [after_created_at, after_id]
    sql += ' ORDER BY created_at ASC, id ASC LIMIT %s'
    params.append(limit)
    return fetch_all(sql, params)


def get_reply_counts(comment_ids: list) -> dict:
    """Batched reply-count aggregate for a page of top-level comments — one
    query for the whole page, not one query per comment (no N+1)."""
    if not comment_ids:
        return {}
    rows = fetch_all(
        'SELECT parent_id, COUNT(*) AS cnt FROM question_discussions '
        'WHERE parent_id = ANY(%s) AND is_deleted=false GROUP BY parent_id',
        (list(comment_ids),),
    )
    return {row['parent_id']: row['cnt'] for row in rows}


def get_comment_parent_info(comment_id: int) -> dict | None:
    """{'id','parent_id','username'} — used to resolve a reply-to-a-reply's
    thread root server-side (parent_id always ends up pointing at the
    top-level comment, keeping the 2-tier flat structure even if a client
    sends a reply's own id as parent_id)."""
    return fetch_one('SELECT id,parent_id,username FROM question_discussions WHERE id=%s', (comment_id,))


def insert_comment(record: dict) -> dict | None:
    return insert_returning('question_discussions', record)


def get_comment_owner(comment_id: int) -> dict | None:
    """{'user_id', 'question_id'} for ownership checks (edit/delete)."""
    return fetch_one('SELECT user_id,question_id FROM question_discussions WHERE id=%s', (comment_id,))


def update_comment_message(comment_id: int, message: str, updated_at_iso: str) -> None:
    execute(
        'UPDATE question_discussions SET message=%s, is_edited=%s, updated_at=%s WHERE id=%s',
        (message, True, updated_at_iso, comment_id),
    )


def soft_delete_comment(comment_id: int) -> None:
    execute('UPDATE question_discussions SET is_deleted=%s WHERE id=%s', (True, comment_id))


def get_comment_pin_state(comment_id: int) -> dict | None:
    return fetch_one('SELECT is_pinned,question_id FROM question_discussions WHERE id=%s', (comment_id,))


def set_comment_pinned(comment_id: int, value: bool) -> None:
    execute('UPDATE question_discussions SET is_pinned=%s WHERE id=%s', (value, comment_id))


def get_comment_best_state(comment_id: int) -> dict | None:
    return fetch_one('SELECT is_best_answer,question_id FROM question_discussions WHERE id=%s', (comment_id,))


def clear_best_answer_for_question(question_id: int) -> None:
    execute('UPDATE question_discussions SET is_best_answer=%s WHERE question_id=%s', (False, question_id))


def set_comment_best(comment_id: int, value: bool) -> None:
    execute('UPDATE question_discussions SET is_best_answer=%s WHERE id=%s', (value, comment_id))


def get_count(question_id: int) -> int:
    row = fetch_one('SELECT count FROM discussion_counts WHERE question_id=%s', (question_id,))
    return row['count'] if row else 0


def get_counts_bulk(question_ids: list) -> dict:
    rows = fetch_all(
        'SELECT question_id,count FROM discussion_counts WHERE question_id = ANY(%s)',
        (question_ids,),
    )
    return {row['question_id']: row['count'] for row in rows}


def upsert_count_delta(question_id: int, delta: int) -> None:
    """
    Atomically apply `delta` to the discussion's comment count, creating the
    row if it doesn't exist yet — a single INSERT ... ON CONFLICT DO UPDATE
    instead of the previous SELECT-then-UPDATE/INSERT (which raced under
    concurrent posts/deletes on the same question, since two requests could
    both read the same starting count before either wrote back).
    discussion_counts.question_id is the table's primary key.
    """
    execute(
        'INSERT INTO discussion_counts (question_id, count) VALUES (%s, GREATEST(0, %s)) '
        'ON CONFLICT (question_id) DO UPDATE SET count = GREATEST(0, discussion_counts.count + %s)',
        (question_id, delta, delta),
    )
