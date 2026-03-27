"""
app/db/questions.py
All Supabase queries related to the `questions` table.

DELETE FIX (v2):
  delete_question() and delete_questions_bulk() now purge FK-dependent
  child rows (responses, question_discussions, discussion_counts) BEFORE
  deleting the question row itself.  Previously, Supabase/Postgres raised
  a FK violation that was silently swallowed, causing delete to appear to
  succeed on the frontend while nothing was actually removed from the DB.

  Correct deletion order per the schema:
    1. responses          (FK → questions.id)
    2. question_discussions (FK → questions.id)
    3. discussion_counts  (FK → questions.id)
    4. questions          (the row itself)
"""

from typing import Optional, List, Dict
from app.db import supabase


_ALL_COLS = (
    "id,exam_id,question_text,option_a,option_b,option_c,option_d,"
    "correct_answer,question_type,image_path,positive_marks,negative_marks,tolerance"
)


def get_questions_by_exam(exam_id: int) -> List[Dict]:
    try:
        res = (
            supabase.table("questions")
            .select(_ALL_COLS)
            .eq("exam_id", exam_id)
            .order("id")
            .execute()
        )
        return res.data or []
    except Exception as e:
        print(f"[db.questions] get_questions_by_exam error: {e}")
        return []


def get_question_by_id(question_id: int) -> Optional[Dict]:
    try:
        res = supabase.table("questions").select(_ALL_COLS).eq("id", question_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.questions] get_question_by_id error: {e}")
        return None


def create_question(question_data: Dict) -> Optional[Dict]:
    try:
        res = supabase.table("questions").insert(question_data).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"[db.questions] create_question error: {e}")
        return None


def create_questions_bulk(questions: List[Dict]) -> bool:
    try:
        supabase.table("questions").insert(questions).execute()
        return True
    except Exception as e:
        print(f"[db.questions] create_questions_bulk error: {e}")
        return False


def update_question(question_id: int, updates: Dict) -> bool:
    try:
        supabase.table("questions").update(updates).eq("id", question_id).execute()
        return True
    except Exception as e:
        print(f"[db.questions] update_question error: {e}")
        return False


def _purge_question_children(question_id: int) -> None:
    """
    Delete all FK-dependent child rows for a question BEFORE deleting
    the question itself.  This prevents FK constraint violations that
    Supabase returns as errors (silently caught → delete appears to work
    but nothing is actually removed).

    Deletion order (safe per schema FK graph):
      1. responses          — FK on question_id
      2. question_discussions — FK on question_id
      3. discussion_counts  — FK on question_id (PK = question_id)
    """
    try:
        supabase.table("responses").delete().eq("question_id", question_id).execute()
    except Exception as e:
        print(f"[db.questions] purge responses for q={question_id}: {e}")

    try:
        supabase.table("question_discussions").delete().eq("question_id", question_id).execute()
    except Exception as e:
        print(f"[db.questions] purge discussions for q={question_id}: {e}")

    try:
        supabase.table("discussion_counts").delete().eq("question_id", question_id).execute()
    except Exception as e:
        print(f"[db.questions] purge discussion_counts for q={question_id}: {e}")


def delete_question(question_id: int) -> bool:
    """
    Delete a single question and ALL FK-dependent child rows.

    FIX: Previously only called DELETE on `questions` directly, which
    triggered a FK violation on `responses`, `question_discussions`, and
    `discussion_counts`.  The exception was silently swallowed making it
    look like success — but nothing was deleted.
    """
    try:
        _purge_question_children(question_id)
        supabase.table("questions").delete().eq("id", question_id).execute()
        return True
    except Exception as e:
        print(f"[db.questions] delete_question error: {e}")
        return False


def delete_questions_bulk(question_ids: List[int]) -> int:
    """
    Delete multiple questions and ALL their FK-dependent child rows.
    Returns count of successfully deleted questions.

    FIX: Same FK violation issue as delete_question() — children must be
    purged first.  We also batch-delete children where possible to reduce
    round-trips (single IN query per child table for the whole batch),
    then delete question rows one-by-one to track per-row success.
    """
    if not question_ids:
        return 0

    # ── Step 1: Batch-purge children for ALL question IDs at once ──────────
    # This is more efficient than looping _purge_question_children() per ID.
    try:
        supabase.table("responses").delete().in_("question_id", question_ids).execute()
    except Exception as e:
        print(f"[db.questions] bulk purge responses: {e}")

    try:
        supabase.table("question_discussions").delete().in_("question_id", question_ids).execute()
    except Exception as e:
        print(f"[db.questions] bulk purge discussions: {e}")

    try:
        supabase.table("discussion_counts").delete().in_("question_id", question_ids).execute()
    except Exception as e:
        print(f"[db.questions] bulk purge discussion_counts: {e}")

    # ── Step 2: Delete question rows ───────────────────────────────────────
    # Try as a batch first (single IN query); fall back to per-row on failure.
    try:
        supabase.table("questions").delete().in_("id", question_ids).execute()
        return len(question_ids)
    except Exception as e:
        print(f"[db.questions] bulk delete (batch) failed, falling back to per-row: {e}")

    deleted = 0
    for qid in question_ids:
        try:
            supabase.table("questions").delete().eq("id", qid).execute()
            deleted += 1
        except Exception as e2:
            print(f"[db.questions] delete_questions_bulk error on {qid}: {e2}")
    return deleted