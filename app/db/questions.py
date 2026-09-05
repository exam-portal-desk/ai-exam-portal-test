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

from typing import Optional, List, Dict, Set
from app.db import fetch_one, fetch_all, execute, set_clause, insert_returning, insert_many, transaction
from app.utils.pagination import paginate_params, pagination_meta, attach_row_numbers


_ALL_COLS = (
    "id,exam_id,question_text,option_a,option_b,option_c,option_d,"
    "correct_answer,question_type,image_path,positive_marks,negative_marks,tolerance,metadata"
)


def get_questions_by_type_counts() -> Dict[str, int]:
    """Question count per question_type — one aggregate query, not a row
    fetch, for the dashboard's Questions-by-Type chart."""
    try:
        rows = fetch_all("SELECT question_type, COUNT(*) AS count FROM questions GROUP BY question_type")
        return {r["question_type"]: r["count"] for r in rows}
    except Exception as e:
        print(f"[db.questions] get_questions_by_type_counts error: {e}")
        return {}


def get_top_exams_by_question_count(limit: int = 5) -> List[Dict]:
    """Exams with the most questions (top N) — one aggregate JOIN query,
    for the dashboard's Exams by Question Count chart."""
    try:
        return fetch_all(
            "SELECT ex.name AS name, COUNT(q.id) AS count FROM questions q "
            "JOIN exams ex ON ex.id = q.exam_id "
            "GROUP BY ex.id, ex.name ORDER BY count DESC LIMIT %s",
            (limit,),
        )
    except Exception as e:
        print(f"[db.questions] get_top_exams_by_question_count error: {e}")
        return []


def get_questions_count() -> int:
    """Total question count (across all exams) via COUNT query — no data fetch."""
    try:
        row = fetch_one("SELECT COUNT(*) AS count FROM questions")
        return row["count"] if row else 0
    except Exception as e:
        print(f"[db.questions] get_questions_count error: {e}")
        return 0


def get_question_by_id(question_id: int) -> Optional[Dict]:
    try:
        return fetch_one(f"SELECT {_ALL_COLS} FROM questions WHERE id=%s", (question_id,))
    except Exception as e:
        print(f"[db.questions] get_question_by_id error: {e}")
        return None


def get_questions_by_exam(exam_id: int) -> List[Dict]:
    """Every question for one exam, unpaginated — used by Import/Export
    (a CSV export means "every question", not "the currently visible
    page") and by anything else that genuinely needs the complete set.
    Manage Questions' own "Load Questions" list uses
    get_questions_by_exam_page() below instead — see its docstring."""
    try:
        return fetch_all(f"SELECT {_ALL_COLS} FROM questions WHERE exam_id=%s ORDER BY id", (exam_id,))
    except Exception as e:
        print(f"[db.questions] get_questions_by_exam error: {e}")
        return []


# Sentinel per_page value for the admin's explicit "Show All" choice on
# Manage Questions — deliberately large rather than "no LIMIT at all" so a
# single malformed/huge exam can't turn one click into an unbounded fetch.
QUESTIONS_SHOW_ALL_PER_PAGE = 5000


def get_questions_by_exam_page(exam_id: int, search: str = "", question_type: str = "",
                                has_image: str = "", page=1, per_page=20) -> Dict:
    """Server-side searched/filtered/paginated question list for one exam —
    Manage Questions' "Load Questions". Replaces fetching and rendering
    EVERY question in the exam on every page load/search keystroke: this
    app's database is a remote Supabase instance, so each round trip costs
    real network latency regardless of query complexity, but the amount of
    HTML generated, sanitized (sanitize_html() per field) and sent to the
    browser scales with row count — that part matters a lot once an exam
    has hundreds/thousands of questions, which get_questions_by_exam()
    above had no way to bound.

    search matches question_text, the question's own id (as text), or its
    question_type — the same three fields the old client-side search box
    matched against, now done in SQL instead of over an in-DOM array.
    question_type/has_image are the existing Type/Image filter dropdowns.
    """
    page, per_page, offset = paginate_params(page, per_page, max_per_page=QUESTIONS_SHOW_ALL_PER_PAGE)
    try:
        where = ["exam_id=%s"]
        params: List = [exam_id]
        if search:
            where.append("(question_text ILIKE %s OR CAST(id AS TEXT) ILIKE %s OR question_type ILIKE %s)")
            like = f"%{search}%"
            params += [like, like, like]
        if question_type:
            where.append("question_type=%s")
            params.append(question_type)
        if has_image == "with":
            where.append("(image_path IS NOT NULL AND image_path <> '')")
        elif has_image == "without":
            where.append("(image_path IS NULL OR image_path = '')")
        where_sql = "WHERE " + " AND ".join(where)

        total = fetch_one(f"SELECT COUNT(*) AS count FROM questions {where_sql}", params)["count"]
        rows = fetch_all(
            f"SELECT {_ALL_COLS} FROM questions {where_sql} ORDER BY id LIMIT %s OFFSET %s",
            params + [per_page, offset],
        )
        attach_row_numbers(rows, page, per_page)
        return {"questions": rows, **pagination_meta(total, page, per_page)}
    except Exception as e:
        print(f"[db.questions] get_questions_by_exam_page error: {e}")
        page, per_page, _ = paginate_params(page, per_page)
        return {"questions": [], **pagination_meta(0, page, per_page)}


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


# ─────────────────────────────────────────────
# Optional per-question metadata (questions.metadata JSONB) — e.g. a
# previous-year source tag like "ESE 2021". Deliberately open-ended: no
# separate column per metadata kind, just keys inside this one JSONB blob.
# ─────────────────────────────────────────────

def build_question_metadata(source_tag: Optional[str]) -> Optional[Dict]:
    """Canonical shape for the metadata this app currently understands —
    the ONE place that decides what a "source tag" value becomes in
    storage. Every create path (single add, batch add, CSV import) should
    build its metadata column value through this, not by hand, so the
    representation never drifts between callers. Returns None for a blank
    tag (nothing to store) — a fresh row's metadata column simply stays NULL."""
    tag = (source_tag or "").strip()
    return {"source_tag": tag} if tag else None


def merge_question_metadata(question_id: int, patch: Dict) -> bool:
    """Shallow-merge `patch` into an EXISTING question's metadata, leaving
    every other key already stored there untouched — e.g. a question saved
    with {"difficulty": "medium", "source_tag": "ESE 2021"} keeps its
    difficulty if only source_tag is edited afterward. A key whose patch
    value is falsy/None is REMOVED from metadata rather than stored as an
    empty string, so clearing the Source Tag field deletes 'source_tag'
    instead of leaving clutter behind. This is the only function that
    should ever mutate metadata after a row already exists — every editor
    (admin single-edit today, anything else later) should call this
    instead of writing `metadata` through update_question() directly,
    which would replace the whole JSONB value."""
    try:
        set_keys = {k: v for k, v in patch.items() if v not in (None, "")}
        remove_keys = [k for k, v in patch.items() if v in (None, "")]
        if set_keys:
            execute(
                "UPDATE questions SET metadata = COALESCE(metadata,'{}'::jsonb) || %s::jsonb WHERE id=%s",
                (set_keys, question_id),
            )
        for k in remove_keys:
            execute(
                "UPDATE questions SET metadata = COALESCE(metadata,'{}'::jsonb) - %s WHERE id=%s",
                (k, question_id),
            )
        return True
    except Exception as e:
        print(f"[db.questions] merge_question_metadata error: {e}")
        return False


def update_questions_by_type(exam_id: int, question_type: str, updates: Dict) -> int:
    """Set-based update for every question of a given type within an exam —
    one statement instead of fetch-all + per-row update_question() calls
    (flagged in the architecture audit). Returns the number of rows updated."""
    try:
        sc, params = set_clause(updates)
        return execute(
            f"UPDATE questions SET {sc} WHERE exam_id=%s AND UPPER(question_type)=UPPER(%s)",
            params + [exam_id, question_type],
        )
    except Exception as e:
        print(f"[db.questions] update_questions_by_type error: {e}")
        return 0


def get_questions_by_ids(exam_id: int, ids: List[int]) -> List[Dict]:
    """Full rows for an arbitrary subset of one exam's questions (e.g. a
    saved selection from Image Mapping's "select all matching" step, which
    may span many list pages) — with a `row_no` that reflects each
    question's TRUE position across the WHOLE exam (via ROW_NUMBER() OVER
    the full ordered set, then filtered down), not its position within
    this arbitrary subset. That's what keeps "Q14" meaning the same thing
    here as it does on the paginated list."""
    if not ids:
        return []
    try:
        return fetch_all(
            f"SELECT {', '.join('t.' + c for c in _ALL_COLS.split(','))}, t.row_no FROM ("
            f"  SELECT {_ALL_COLS}, ROW_NUMBER() OVER (ORDER BY id) AS row_no "
            "   FROM questions WHERE exam_id=%s"
            ") t WHERE t.id = ANY(%s) ORDER BY t.id",
            (exam_id, ids),
        )
    except Exception as e:
        print(f"[db.questions] get_questions_by_ids error: {e}")
        return []


def get_question_ids_for_exam(exam_id: int, ids: List[int]) -> Set[int]:
    """Which of `ids` actually belong to `exam_id` — used by the Image
    Mapping bulk-save to reject any question id that doesn't belong to the
    exam the admin selected, BEFORE writing anything (see
    bulk_set_image_paths() below). Returns a set for cheap membership
    checks against the requested id list."""
    if not ids:
        return set()
    try:
        rows = fetch_all(
            "SELECT id FROM questions WHERE exam_id=%s AND id = ANY(%s)",
            (exam_id, ids),
        )
        return {r["id"] for r in rows}
    except Exception as e:
        print(f"[db.questions] get_question_ids_for_exam error: {e}")
        return set()


def bulk_set_image_paths(exam_id: int, mappings: List[Dict]) -> List[Dict]:
    """Set `image_path` for many questions in one atomic round trip — the
    Image Mapping feature's bulk save. `mappings` is a list of
    {"id": question_id, "image_path": str|None} (None clears the image).
    Mirrors app/db/notes.py's existing bulk-VALUES pattern: a single
    UPDATE ... FROM (VALUES ...) inside one transaction, so a save either
    fully applies or fully rolls back — never a partially-updated exam.
    `AND q.exam_id=%s` is defense-in-depth on top of the caller's own
    get_question_ids_for_exam() pre-check. Returns the updated rows
    (id, image_path) so the caller can resolve fresh image URLs without a
    second fetch."""
    if not mappings:
        return []
    try:
        with transaction() as cur:
            values_sql = ", ".join(["(%s::int,%s::text)"] * len(mappings))
            params: List = []
            for m in mappings:
                params += [m["id"], m["image_path"]]
            cur.execute(
                "UPDATE questions AS q SET image_path=v.image_path "
                f"FROM (VALUES {values_sql}) AS v(id,image_path) "
                "WHERE q.id=v.id AND q.exam_id=%s "
                "RETURNING q.id, q.image_path",
                params + [exam_id],
            )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        print(f"[db.questions] bulk_set_image_paths error: {e}")
        return []


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
