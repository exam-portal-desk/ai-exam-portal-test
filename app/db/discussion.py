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


def get_thread_rows(question_id: int) -> list:
    """All non-deleted-or-ghost rows for a question, oldest first."""
    return fetch_all(
        'SELECT id,question_id,exam_id,user_id,username,message,parent_id,is_pinned,is_best_answer,'
        'is_edited,created_at,updated_at,is_deleted FROM question_discussions '
        'WHERE question_id=%s ORDER BY created_at ASC',
        (question_id,),
    )


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
