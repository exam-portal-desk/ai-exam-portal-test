"""
app/services/ranking_service.py
Exam Performance & Ranking dashboard — orchestrates app/db/results.py's
ranking queries into the shapes templates/result.html consumes.

Ranking rules (see plan / user confirmation):
  - Eligible participant = role='user' account, BEST completed attempt for
    the exam (highest percentage, tie-broken by score then earliest
    completion). Enforced once in app/db/results.py's shared CTE.
  - Live: recomputed from `results`/`users` on every call, no snapshot.
  - Ties: single rank key (percentage DESC) via SQL RANK() -> 1,2,2,4.
  - Percentile: (participants strictly below) / (total participants) * 100.
    "Top X%" is always derived from that same percentile, never a separate
    rank/total division.
"""

import math
from typing import Dict, List, Optional

from app.db.results import (
    get_exam_viewer_rank, get_exam_leaderboard_page, get_exam_leaderboard_more,
    get_exam_performance_stats, get_exam_score_distribution,
)
from app.services.image_storage_service import profile_photo_url_from_key
from app.utils.datetime_service import format_display

_BUCKET_LABELS = {1: "0-20", 2: "20-40", 3: "40-60", 4: "60-80", 5: "80-100"}
_TOP10_LIMIT = 10
_LOAD_MORE_LIMIT = 20
_LOAD_MORE_MAX = 50


# ─────────────────────────────────────────────
# Row shaping — shared by the initial page render and the AJAX "load more"
# endpoint, so leaderboard markup/fields never drift between the two.
# ─────────────────────────────────────────────

def _display_name(row: Dict) -> str:
    return row.get("full_name") or row.get("username") or "Participant"


def _initials(name: str) -> str:
    return (name.strip()[:1] or "?").upper()


def _avatar_color_class(name: str) -> str:
    n = ord((name or "?")[0].upper()) % 5
    return "" if n == 0 else f" av-{n + 1}"


def _row_accuracy(row: Dict) -> Optional[float]:
    correct = int(row.get("correct_answers") or 0)
    incorrect = int(row.get("incorrect_answers") or 0)
    attempted = correct + incorrect
    if attempted <= 0:
        return None
    return round(correct / attempted * 100, 1)


def shape_leaderboard_row(row: Dict, viewer_student_id: int) -> Dict:
    name = _display_name(row)
    return {
        "rank": int(row["rank"]),
        "name": name,
        "initials": _initials(name),
        "avatar_class": _avatar_color_class(name),
        "avatar_url": profile_photo_url_from_key(row.get("profile_photo_key")),
        "score": row.get("score"),
        "max_score": row.get("max_score"),
        "percentage": round(float(row.get("percentage") or 0), 2),
        "accuracy": _row_accuracy(row),
        "time_taken_minutes": row.get("time_taken_minutes"),
        "is_current_user": int(row["student_id"]) == int(viewer_student_id),
    }


def shape_leaderboard_rows(rows: List[Dict], viewer_student_id: int) -> List[Dict]:
    return [shape_leaderboard_row(r, viewer_student_id) for r in rows]


# ─────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────

def build_exam_performance(exam: Dict, result: Dict, user_id: int) -> Dict:
    """
    Build the full ranking/analytics context for the result page. Returns
    {"available": False, "reason": ...} when ranking can't be shown (not
    enough eligible participants, or the viewer's own attempt isn't
    eligible — e.g. an admin/test account). Never touches score
    calculation — purely reads already-finalized `results` rows.
    """
    exam_id = int(exam["id"])

    viewer = get_exam_viewer_rank(exam_id, user_id)
    if not viewer:
        return {"available": False, "reason": "Ranking is not available for this account."}

    total_participants = int(viewer["total_participants"])
    if total_participants < 2:
        return {
            "available": False,
            "reason": "Ranking will be available once more participants complete this exam.",
        }

    rank = int(viewer["rank"])
    count_below = int(viewer["rank_asc"]) - 1
    percentile = round(count_below / total_participants * 100, 1)
    top_percent = max(1, math.ceil(100 - percentile))

    viewing_is_best_attempt = int(viewer["id"]) == int(result["id"])
    best_attempt_note = None
    if not viewing_is_best_attempt:
        try:
            submitted_str = format_display(viewer.get("completed_at")) or "earlier"
        except Exception:
            submitted_str = "earlier"
        best_attempt_note = (
            f"Ranking reflects your best attempt: {viewer.get('score')}/{viewer.get('max_score')} "
            f"({round(float(viewer.get('percentage') or 0), 2)}%), submitted {submitted_str}."
        )

    total_q = int(viewer.get("total_questions") or 0)
    time_taken = viewer.get("time_taken_minutes") or 0
    avg_time_per_q = round(time_taken * 60 / total_q, 1) if total_q > 0 else None

    passing_percentage = exam.get("passing_percentage")
    stats = get_exam_performance_stats(exam_id, passing_percentage) or {}

    passing_summary = None
    if passing_percentage is not None:
        passing_summary = {
            "cutoff": round(float(passing_percentage), 2),
            "passed": float(viewer.get("percentage") or 0) >= float(passing_percentage),
            "passed_count": stats.get("passed_count"),
            "failed_count": stats.get("failed_count"),
        }

    summary = {
        "score": viewer.get("score"),
        "max_score": viewer.get("max_score"),
        "percentage": round(float(viewer.get("percentage") or 0), 2),
        "rank": rank,
        "total_participants": total_participants,
        "percentile": percentile,
        "top_percent": top_percent,
        "accuracy": _row_accuracy(viewer),
        "correct_answers": viewer.get("correct_answers"),
        "incorrect_answers": viewer.get("incorrect_answers"),
        "unanswered_questions": viewer.get("unanswered_questions"),
        "total_questions": total_q,
        "time_taken_minutes": time_taken,
        "avg_time_per_question_secs": avg_time_per_q,
        "passing": passing_summary,
    }

    # ── Leaderboard: top 10 + viewer's own row if outside it ──
    page_rows = get_exam_leaderboard_page(exam_id, user_id, limit=_TOP10_LIMIT)
    shaped = shape_leaderboard_rows(page_rows, user_id)
    top10 = [r for r in shaped if r["rank"] <= _TOP10_LIMIT]
    viewer_row = next((r for r in shaped if r["is_current_user"]), None)
    in_top10 = bool(viewer_row and viewer_row["rank"] <= _TOP10_LIMIT)
    user_position = None if in_top10 else viewer_row

    # ── Score distribution ──
    dist_rows = {int(r["bucket"]): int(r["count"]) for r in get_exam_score_distribution(exam_id)}
    max_bucket_count = max(dist_rows.values()) if dist_rows else 0
    viewer_pct = summary["percentage"]
    if viewer_pct < 0:
        viewer_bucket = 1
    elif viewer_pct >= 100:
        viewer_bucket = 5
    else:
        viewer_bucket = min(5, int(viewer_pct // 20) + 1)

    distribution = [
        {
            "label": _BUCKET_LABELS[b],
            "count": dist_rows.get(b, 0),
            "height_pct": round(dist_rows.get(b, 0) / max_bucket_count * 100, 1) if max_bucket_count else 0,
            "is_viewer_bucket": b == viewer_bucket,
        }
        for b in range(1, 6)
    ]

    # ── Comparison vs. average / highest / median / lowest ──
    avg_accuracy = stats.get("avg_accuracy")
    comparison = {
        "you": {
            "score": summary["score"],
            "percentage": summary["percentage"],
            "accuracy": summary["accuracy"],
            "time_taken_minutes": summary["time_taken_minutes"],
        },
        "average": {
            "score": round(float(stats["avg_score"]), 1) if stats.get("avg_score") is not None else None,
            "percentage": round(float(stats["avg_percentage"]), 1) if stats.get("avg_percentage") is not None else None,
            "accuracy": round(float(avg_accuracy), 1) if avg_accuracy is not None else None,
            "time_taken_minutes": round(float(stats["avg_time_taken_minutes"]), 1) if stats.get("avg_time_taken_minutes") is not None else None,
        },
        "highest": round(float(stats["max_percentage"]), 2) if stats.get("max_percentage") is not None else None,
        "median": round(float(stats["median_percentage"]), 2) if stats.get("median_percentage") is not None else None,
        "lowest": round(float(stats["min_percentage"]), 2) if stats.get("min_percentage") is not None else None,
    }

    return {
        "available": True,
        "reason": None,
        "viewing_is_best_attempt": viewing_is_best_attempt,
        "best_attempt_note": best_attempt_note,
        "summary": summary,
        "top10": top10,
        "user_position": user_position,
        "in_top10": in_top10,
        "distribution": distribution,
        "comparison": comparison,
        "load_more_limit": _LOAD_MORE_LIMIT,
        "has_more": total_participants > _TOP10_LIMIT,
    }


def get_more_leaderboard_rows(exam_id: int, user_id: int, after_rank: int, limit: int) -> List[Dict]:
    """Backs the 'View full leaderboard' AJAX endpoint. `limit` is
    server-side capped regardless of what the client requests."""
    limit = max(1, min(int(limit or _LOAD_MORE_LIMIT), _LOAD_MORE_MAX))
    rows = get_exam_leaderboard_more(int(exam_id), int(after_rank), limit=limit)
    return shape_leaderboard_rows(rows, user_id)
