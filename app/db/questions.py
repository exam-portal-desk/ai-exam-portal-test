"""
app/db/questions.py
All PostgreSQL queries related to the `questions` table.

DELETE FIX (v2):
  delete_question() and delete_questions_bulk() now purge FK-dependent
  child rows (responses, question_discussions, discussion_counts) BEFORE
  deleting the question row itself.

  Correct deletion order per the schema:
    1. responses            (FK -> questions.id)
    2. question_discussions (FK -> questions.id)
    3. discussion_counts    (FK -> questions.id)
    4. questions             (the row itself)
"""

from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, execute, set_clause, insert_returning, insert_many


_ALL_COLS = (
    "id,exam_id,question_text,option_a,option_b,option_c,option_d,"
    "correct_answer,question_type,image_path,positive_marks,negative_marks,tolerance"
)


def get_question_by_id(question_id: int) -> Optional[Dict]:
    try:
        return fetch_one(f"SELECT {_ALL_COLS} FROM questions WHERE id=%s", (question_id,))
    except Exception as e:
        print(f"[db.questions] get_question_by_id error: {e}")
        return None


def get_questions_by_exam(exam_id: int) -> List[Dict]:
    try:
        return fetch_all(f"SELECT {_ALL_COLS} FROM questions WHERE exam_id=%s ORDER BY id", (exam_id,))
    except Exception as e:
        print(f"[db.questions] get_questions_by_exam error: {e}")
        return []


def create_question(question_data: Dict) -> Optional[Dict]:
    try:
        return insert_returning("questions", question_data)
    except Exception as e:
        print(f"[db.questions] create_question error: {e}")
        return None


def create_questions_bulk(questions: List[Dict]) -> bool:
    try:
        insert_many("questions", questions)
        return True
    except Exception as e:
        print(f"[db.questions] create_questions_bulk error: {e}")
        return False


def update_question(question_id: int, updates: Dict) -> bool:
    try:
        sc, params = set_clause(updates)
        execute(f"UPDATE questions SET {sc} WHERE id=%s", params + [question_id])
        return True
    except Exception as e:
        print(f"[db.questions] update_question error: {e}")
        return False


def _purge_question_children(question_id: int) -> None:
    """
    Delete all FK-dependent child rows for a question BEFORE deleting
    the question itself, to avoid FK constraint violations.

    Deletion order (safe per schema FK graph):
      1. responses            - FK on question_id
      2. question_discussions - FK on question_id
      3. discussion_counts    - FK on question_id (PK = question_id)
    """
    try:
        execute("DELETE FROM responses WHERE question_id=%s", (question_id,))
    except Exception as e:
        print(f"[db.questions] purge responses for q={question_id}: {e}")

    try:
        execute("DELETE FROM question_discussions WHERE question_id=%s", (question_id,))
    except Exception as e:
        print(f"[db.questions] purge discussions for q={question_id}: {e}")

    try:
        execute("DELETE FROM discussion_counts WHERE question_id=%s", (question_id,))
    except Exception as e:
        print(f"[db.questions] purge discussion_counts for q={question_id}: {e}")


def delete_question(question_id: int) -> bool:
    """Delete a single question and ALL FK-dependent child rows."""
    try:
        _purge_question_children(question_id)
        execute("DELETE FROM questions WHERE id=%s", (question_id,))
        return True
    except Exception as e:
        print(f"[db.questions] delete_question error: {e}")
        return False


def delete_questions_bulk(question_ids: List[int]) -> int:
    """
    Delete multiple questions and ALL their FK-dependent child rows.
    Returns count of successfully deleted questions.
    """
    if not question_ids:
        return 0

    try:
        execute("DELETE FROM responses WHERE question_id = ANY(%s)", (question_ids,))
    except Exception as e:
        print(f"[db.questions] bulk purge responses: {e}")

    try:
        execute("DELETE FROM question_discussions WHERE question_id = ANY(%s)", (question_ids,))
    except Exception as e:
        print(f"[db.questions] bulk purge discussions: {e}")

    try:
        execute("DELETE FROM discussion_counts WHERE question_id = ANY(%s)", (question_ids,))
    except Exception as e:
        print(f"[db.questions] bulk purge discussion_counts: {e}")

    try:
        execute("DELETE FROM questions WHERE id = ANY(%s)", (question_ids,))
        return len(question_ids)
    except Exception as e:
        print(f"[db.questions] bulk delete (batch) failed, falling back to per-row: {e}")

    deleted = 0
    for qid in question_ids:
        try:
            execute("DELETE FROM questions WHERE id=%s", (qid,))
            deleted += 1
        except Exception as e2:
            print(f"[db.questions] delete_questions_bulk error on {qid}: {e2}")
    return deleted
