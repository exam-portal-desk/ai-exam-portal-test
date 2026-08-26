"""
app/db/results.py
All PostgreSQL queries for `results` and `responses` tables.
"""

from typing import Optional, List, Dict
from app.db import fetch_one, fetch_all, insert_returning, insert_many


# ─────────────────────────────────────────────
# Results
# ─────────────────────────────────────────────

_RESULT_COLS = (
    "id,student_id,exam_id,score,max_score,percentage,grade,"
    "completed_at,time_taken_minutes,correct_answers,incorrect_answers,"
    "unanswered_questions,total_questions"
)


def get_all_results() -> List[Dict]:
    try:
        return fetch_all(f"SELECT {_RESULT_COLS} FROM results ORDER BY completed_at DESC")
    except Exception as e:
        print(f"[db.results] get_all_results error: {e}")
        return []


def get_result_by_id(result_id: int) -> Optional[Dict]:
    try:
        return fetch_one(f"SELECT {_RESULT_COLS} FROM results WHERE id=%s", (result_id,))
    except Exception as e:
        print(f"[db.results] get_result_by_id error: {e}")
        return None


def get_results_by_user(user_id: int) -> List[Dict]:
    try:
        return fetch_all(
            f"SELECT {_RESULT_COLS} FROM results WHERE student_id=%s ORDER BY completed_at DESC", (user_id,)
        )
    except Exception as e:
        print(f"[db.results] get_results_by_user error: {e}")
        return []


def get_results_by_exam(exam_id: int) -> List[Dict]:
    try:
        return fetch_all(
            f"SELECT {_RESULT_COLS} FROM results WHERE exam_id=%s ORDER BY completed_at DESC", (exam_id,)
        )
    except Exception as e:
        print(f"[db.results] get_results_by_exam error: {e}")
        return []


def get_latest_result_by_user_exam(user_id: int, exam_id: int) -> Optional[Dict]:
    try:
        return fetch_one(
            f"SELECT {_RESULT_COLS} FROM results WHERE student_id=%s AND exam_id=%s "
            "ORDER BY completed_at DESC LIMIT 1",
            (user_id, exam_id),
        )
    except Exception as e:
        print(f"[db.results] get_latest_result_by_user_exam error: {e}")
        return None


def create_result(result_data: Dict) -> Optional[Dict]:
    try:
        return insert_returning("results", result_data)
    except Exception as e:
        print(f"[db.results] create_result error: {e}")
        return None


# ─────────────────────────────────────────────
# Responses
# ─────────────────────────────────────────────

_RESPONSE_COLS = (
    "id,result_id,exam_id,question_id,given_answer,correct_answer,"
    "is_correct,marks_obtained,question_type,is_attempted"
)


def get_responses_by_result(result_id: int) -> List[Dict]:
    try:
        return fetch_all(
            f"SELECT {_RESPONSE_COLS} FROM responses WHERE result_id=%s ORDER BY question_id", (result_id,)
        )
    except Exception as e:
        print(f"[db.results] get_responses_by_result error: {e}")
        return []


def create_responses_bulk(responses: List[Dict]) -> bool:
    try:
        insert_many("responses", responses)
        return True
    except Exception as e:
        print(f"[db.results] create_responses_bulk error: {e}")
        return False


# ─────────────────────────────────────────────
# Exam ranking / leaderboard
#
# "Eligible participant" = a user whose `role` includes 'user' (excludes
# admin-only/test accounts). users.role is a comma-joined composite string
# (e.g. "user", "admin", "user,admin" — see session_guard.py's own
# "admin" in role / "user" not in role checks), not a strict enum, so this
# is a token match, not a plain equality — an account that is BOTH an admin
# and a genuine exam-taking user still counts. Each eligible user's BEST
# completed attempt (highest percentage, tie-broken by score then earliest
# completion) is used — DISTINCT ON picks that single row per student in
# one pass. Every function below shares this exact CTE text so a
# participant is counted identically everywhere (leaderboard, stats,
# distribution) — never recomputed differently per view.
# ─────────────────────────────────────────────

_BEST_ATTEMPTS_CTE = """
    WITH best_attempts AS (
        SELECT DISTINCT ON (r.student_id)
            r.id, r.student_id, r.score, r.max_score, r.percentage, r.grade,
            r.completed_at, r.time_taken_minutes, r.correct_answers,
            r.incorrect_answers, r.unanswered_questions, r.total_questions
        FROM results r
        JOIN users u ON u.id = r.student_id
        WHERE r.exam_id = %s AND 'user' = ANY(string_to_array(u.role, ','))
        ORDER BY r.student_id, r.percentage DESC, r.score DESC, r.completed_at ASC
    )
"""

_RANKED_CTE = _BEST_ATTEMPTS_CTE + """
    , ranked AS (
        SELECT ba.*,
               u.username, u.full_name, u.profile_photo_key,
               RANK() OVER (ORDER BY ba.percentage DESC) AS rank,
               RANK() OVER (ORDER BY ba.percentage ASC)  AS rank_asc,
               COUNT(*) OVER ()                          AS total_participants
        FROM best_attempts ba
        JOIN users u ON u.id = ba.student_id
    )
"""


def get_exam_leaderboard_page(exam_id: int, viewer_student_id: int, limit: int = 10) -> List[Dict]:
    """
    Top `limit` ranked rows, PLUS the viewer's own row if they fall outside
    that page — always a small, bounded result set (never "load everyone").
    """
    try:
        query = _RANKED_CTE + """
            SELECT * FROM ranked
            WHERE rank <= %s OR student_id = %s
            ORDER BY rank ASC, student_id ASC
        """
        return fetch_all(query, (exam_id, limit, viewer_student_id))
    except Exception as e:
        print(f"[db.results] get_exam_leaderboard_page error: {e}")
        return []


def get_exam_leaderboard_more(exam_id: int, after_rank: int, limit: int = 20) -> List[Dict]:
    """Next page of the leaderboard, strictly after `after_rank` — backs the
    'View full leaderboard' load-more control. `limit` should already be
    server-side capped by the caller."""
    try:
        query = _RANKED_CTE + """
            SELECT * FROM ranked
            WHERE rank > %s
            ORDER BY rank ASC, student_id ASC
            LIMIT %s
        """
        return fetch_all(query, (exam_id, after_rank, limit))
    except Exception as e:
        print(f"[db.results] get_exam_leaderboard_more error: {e}")
        return []


def get_exam_viewer_rank(exam_id: int, student_id: int) -> Optional[Dict]:
    """Single ranked row for one student — used to resolve the viewer's own
    rank/percentile without pulling the whole leaderboard."""
    try:
        query = _RANKED_CTE + "SELECT * FROM ranked WHERE student_id = %s"
        return fetch_one(query, (exam_id, student_id))
    except Exception as e:
        print(f"[db.results] get_exam_viewer_rank error: {e}")
        return None


def get_exam_performance_stats(exam_id: int, passing_percentage: Optional[float] = None) -> Optional[Dict]:
    """Single aggregate row over all eligible best-attempts: counts,
    avg/median/max/min percentage & score, avg time, avg accuracy, and
    (only when a cutoff is supplied) passed/failed counts."""
    try:
        if passing_percentage is not None:
            pass_cols = """
                , COUNT(*) FILTER (WHERE percentage >= %s) AS passed_count,
                  COUNT(*) FILTER (WHERE percentage <  %s) AS failed_count
            """
            params = (exam_id, passing_percentage, passing_percentage)
        else:
            pass_cols = ", NULL AS passed_count, NULL AS failed_count"
            params = (exam_id,)

        query = _BEST_ATTEMPTS_CTE + f"""
            SELECT
                COUNT(*)                                                    AS total_participants,
                AVG(percentage)                                             AS avg_percentage,
                MAX(percentage)                                             AS max_percentage,
                MIN(percentage)                                             AS min_percentage,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY percentage)     AS median_percentage,
                AVG(score)                                                  AS avg_score,
                AVG(time_taken_minutes)                                     AS avg_time_taken_minutes,
                AVG(
                    CASE WHEN (correct_answers + incorrect_answers) > 0
                         THEN correct_answers::numeric / (correct_answers + incorrect_answers) * 100
                         ELSE NULL END
                )                                                            AS avg_accuracy
                {pass_cols}
            FROM best_attempts
        """
        return fetch_one(query, params)
    except Exception as e:
        print(f"[db.results] get_exam_performance_stats error: {e}")
        return None


def get_exam_score_distribution(exam_id: int) -> List[Dict]:
    """5-bucket (0-20/20-40/.../80-100) histogram computed in SQL — always a
    handful of rows back, regardless of participant count."""
    try:
        # Negative marking can push percentage below 0; clamp both ends into
        # the outer buckets rather than letting width_bucket produce 0 or 6.
        query = _BEST_ATTEMPTS_CTE + """
            SELECT GREATEST(1, LEAST(width_bucket(percentage, 0, 100, 5), 5)) AS bucket,
                   COUNT(*) AS count
            FROM best_attempts
            GROUP BY bucket
            ORDER BY bucket
        """
        return fetch_all(query, (exam_id,))
    except Exception as e:
        print(f"[db.results] get_exam_score_distribution error: {e}")
        return []
