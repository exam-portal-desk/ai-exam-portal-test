"""
app/services/result_service.py
Business logic for results:
  - Result visibility gating (instant / delayed / manual)
  - Student analytics calculation
"""

from datetime import datetime, timedelta
from typing import Tuple, List, Dict, Optional

import pandas as pd


# ─────────────────────────────────────────────
# Visibility gating
# ─────────────────────────────────────────────

def can_user_see_result(exam: dict, result: dict) -> Tuple[bool, str]:
    """
    Returns (is_visible, reason_string).
    Defaults to instant (visible) when result_mode is missing.
    """
    mode = (exam.get("result_mode") or "instant").strip().lower()

    if mode in ("instant", ""):
        return True, ""

    if mode == "manual":
        if exam.get("results_released"):
            return True, ""
        return False, "Results have not been released by your instructor yet. Please check back later."

    if mode == "delayed":
        delay_minutes = 0
        try:
            delay_minutes = int(exam.get("result_delay") or 0)
        except (ValueError, TypeError):
            delay_minutes = 0

        if delay_minutes <= 0:
            return True, ""

        completed_at = result.get("completed_at")
        if not completed_at:
            return True, ""

        try:
            if isinstance(completed_at, str):
                submitted_dt = datetime.fromisoformat(
                    completed_at.replace("Z", "+00:00").replace("+00:00", "")
                )
            else:
                submitted_dt = completed_at

            visible_after = submitted_dt + timedelta(minutes=delay_minutes)
            now = datetime.now()

            if now >= visible_after:
                return True, ""

            remaining_secs = int((visible_after - now).total_seconds())
            h = remaining_secs // 3600
            m = (remaining_secs % 3600) // 60
            s = remaining_secs % 60

            time_str = (
                f"{h}h {m}m" if h > 0
                else f"{m}m {s}s" if m > 0
                else f"{remaining_secs}s"
            )
            unlock = visible_after.strftime("%d %B %Y at %I:%M %p")
            return False, f"Your result will be available in {time_str} (at {unlock})."

        except Exception as e:
            print(f"[result_service] can_user_see_result error: {e}")
            return True, ""

    # Unknown mode — safe fallback
    return True, ""


# ─────────────────────────────────────────────
# Student analytics
# ─────────────────────────────────────────────

def calculate_student_analytics(
    results_list: List[Dict],
    exams_list: List[Dict],
    user_id: int,
) -> Dict:
    """
    Compute analytics summary, trends, and grade distribution
    from a list of result dicts.
    """
    if not results_list:
        return {}

    try:
        df = pd.DataFrame(results_list)

        # Parse timestamps
        def _parse_dt(val):
            if not val:
                return None
            try:
                import re
                s = re.sub(r"\.\d+", "", str(val).strip())
                s = s.replace("T", " ")
                s = re.sub(r"[+-]\d{2}:\d{2}$", "", s).strip()
                return pd.Timestamp(s)
            except Exception:
                return None

        df["completed_at"] = df["completed_at"].apply(_parse_dt)
        df["percentage"] = pd.to_numeric(df["percentage"], errors="coerce").fillna(0)
        df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(0)
        df["max_score"] = pd.to_numeric(df["max_score"], errors="coerce").fillna(0)

        df_asc = df.sort_values("completed_at", ascending=True, na_position="first")
        df_desc = df.sort_values("completed_at", ascending=False, na_position="last")

        exams_map = {str(e["id"]): e.get("name", f"Exam {e['id']}") for e in (exams_list or [])}

        def _exam_name(exam_id) -> str:
            return exams_map.get(str(exam_id), "Unknown Exam")

        def _fmt(val) -> Optional[str]:
            try:
                return val.strftime("%d %b %Y, %H:%M")
            except Exception:
                return None

        grade_counts = df["grade"].value_counts().to_dict()
        total_grades = sum(grade_counts.values()) or 1

        score_trend = [
            {
                "exam_name": _exam_name(row["exam_id"]),
                "score": float(row["percentage"]),
                "grade": row["grade"],
                "date": _fmt(row["completed_at"]) or "",
            }
            for _, row in df_asc.iterrows()
        ]

        recent_perf = [
            {
                "exam_name": _exam_name(row["exam_id"]),
                "score": f"{int(row['score'])}/{int(row['max_score'])}",
                "percentage": float(row["percentage"]),
                "grade": row["grade"],
                "date": _fmt(row["completed_at"]),
            }
            for _, row in df_desc.head(10).iterrows()
        ]

        if len(df_asc) >= 2:
            recent_avg = df_asc.tail(3)["percentage"].mean()
            earlier = df_asc.iloc[:-3]
            earlier_avg = (
                earlier["percentage"].mean() if len(earlier) > 0
                else float(df_asc.iloc[0]["percentage"])
            )
            improvement = round(recent_avg - earlier_avg, 2)
        else:
            improvement = 0

        return {
            "total_exams": len(df),
            "average_score": round(float(df["percentage"].mean()), 2),
            "highest_score": round(float(df["percentage"].max()), 2),
            "lowest_score": round(float(df["percentage"].min()), 2),
            "grade_distribution": {
                g: {"count": c, "percentage": round(c / total_grades * 100, 1)}
                for g, c in grade_counts.items()
            },
            "score_trend": score_trend,
            "recent_performance": recent_perf,
            "improvement_trend": improvement,
        }

    except Exception as e:
        print(f"[result_service] calculate_student_analytics error: {e}")
        import traceback
        traceback.print_exc()
        return {}