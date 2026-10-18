"""
app/services/student_analytics_service.py
Data behind the student Analytics dashboard (templates/student_analytics.html), computed only from the signed-in student's
own results.

Isolation: build_dashboard() takes the user id from the server session and nothing else; every query is scoped by it
(results by student_id, responses only for that student's result ids). No filter value can widen that scope.

Only real data is reported: results (score, percentage, grade, counts, time), the exam each belongs to and that exam's
category / subcategory, and the student's own per-question responses (question type, correct / attempted). Results the
student may not see yet (manual / delayed release) are excluded exactly like the Results pages do, and counted separately.
There is no per-question "subject" in the data model, so there is no subject breakdown or filter.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.db.results import get_results_by_user
from app.utils.datetime_service import app_timezone, to_app_tz

MAX_RANGE_DAYS = 3660


class FilterError(ValueError):
    pass


def _date(value) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise FilterError("Dates must be YYYY-MM-DD.")


def _int(value, name) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        n = int(str(value).strip())
    except ValueError:
        raise FilterError(f"{name} is not valid.")
    if n <= 0:
        raise FilterError(f"{name} is not valid.")
    return n


def parse_filters(args) -> Dict:
    """Validated filters from a query string. Unknown keys (including any attempt to name another user) are ignored."""
    f = {
        "from": _date(args.get("from")), "to": _date(args.get("to")),
        "exam_id": _int(args.get("exam_id"), "Exam"),
        "category_id": _int(args.get("category_id"), "Category"),
        "subcategory_id": _int(args.get("subcategory_id"), "Subcategory"),
        "kind": args.get("kind") or "",
    }
    if f["kind"] not in ("", "scheduled", "regular"):
        raise FilterError("Exam type is not valid.")
    if f["from"] and f["to"] and f["from"] > f["to"]:
        f["from"], f["to"] = f["to"], f["from"]
    if f["from"] and f["to"]:
        span = (datetime.strptime(f["to"], "%Y-%m-%d") - datetime.strptime(f["from"], "%Y-%m-%d")).days
        if span > MAX_RANGE_DAYS:
            raise FilterError("That date range is too long.")
    return f


def _bounds(f: Dict):
    """(start, end) as naive UTC — completed_at is stored in UTC while the date pickers speak local days."""
    tz = app_timezone()

    def utc(day, end=False):
        d = datetime.strptime(day, "%Y-%m-%d") + (timedelta(days=1) if end else timedelta())
        return d.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)

    return (utc(f["from"]) if f["from"] else None), (utc(f["to"], end=True) if f["to"] else None)


def _naive_utc(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _avg(values) -> Optional[float]:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _load(user_id: int):
    """(results, exam_map, category_names, subcategory_names, response_stats_fn) — separated so tests can replace it."""
    from app.db.exams import get_exams_by_ids_full
    from app.db.categories import get_all_categories
    from app.db.subcategories import get_all_subcategories
    results = get_results_by_user(user_id)
    exam_map = get_exams_by_ids_full(sorted({int(r["exam_id"]) for r in results if r.get("exam_id")}))
    cats = {int(c["id"]): c.get("name", "") for c in get_all_categories()}
    subs = {int(s["id"]): s.get("name", "") for s in get_all_subcategories()}
    return results, exam_map, cats, subs


def _response_stats(result_ids: List[int], user_id: int) -> List[Dict]:
    from app.db.results import get_response_stats_for_results
    return get_response_stats_for_results(result_ids, user_id)


def build_dashboard(user_id: int, f: Dict, loader=None, stats_loader=None) -> Dict:
    from app.services.result_service import can_user_see_result
    results, exam_map, cat_names, sub_names = (loader or _load)(user_id)
    stats_loader = stats_loader or (lambda ids: _response_stats(ids, user_id))

    def exam_of(r):
        return exam_map.get(str(r.get("exam_id"))) or exam_map.get(r.get("exam_id")) or {}

    # Results this student may see right now; the rest are only counted.
    visible, hidden = [], 0
    for r in results:
        if r.get("student_id") not in (None, user_id):          # belt and braces: never aggregate somebody else's row
            continue
        if can_user_see_result(exam_of(r), r)[0]:
            visible.append(r)
        else:
            hidden += 1

    def enrich(r):
        ex = exam_of(r)
        done = _naive_utc(r.get("completed_at"))
        local = to_app_tz(done) if done else None
        return {
            "result_id": int(r["id"]), "exam_id": int(r.get("exam_id") or 0),
            "exam": ex.get("name") or "Unknown exam",
            "category_id": ex.get("category_id"), "subcategory_id": ex.get("subcategory_id"),
            "category": cat_names.get(int(ex["category_id"])) if ex.get("category_id") else None,
            "subcategory": sub_names.get(int(ex["subcategory_id"])) if ex.get("subcategory_id") else None,
            "scheduled": bool(ex.get("scheduled_mode")),
            "percentage": round(_f(r.get("percentage")), 1), "score": _f(r.get("score")), "max_score": _f(r.get("max_score")),
            "grade": r.get("grade") or "N/A",
            "correct": int(r.get("correct_answers") or 0), "incorrect": int(r.get("incorrect_answers") or 0),
            "unanswered": int(r.get("unanswered_questions") or 0), "total": int(r.get("total_questions") or 0),
            "minutes": int(r.get("time_taken_minutes") or 0),
            "at": done, "date": local.strftime("%Y-%m-%d") if local else None,
            "label": local.strftime("%d %b %Y, %H:%M") if local else "",
        }

    rows = [enrich(r) for r in visible]

    # Dropdown options come from everything the student can see, so narrowing one filter never empties another.
    exam_opts = {}
    for x in rows:
        o = exam_opts.setdefault(x["exam_id"], {"id": x["exam_id"], "name": x["exam"], "category_id": x["category_id"],
                                                "subcategory_id": x["subcategory_id"], "category": x["category"],
                                                "subcategory": x["subcategory"], "attempts": 0})
        o["attempts"] += 1
    options = {
        "exams": sorted(exam_opts.values(), key=lambda o: o["name"].lower()),
        "categories": sorted({(x["category_id"], x["category"]) for x in rows if x["category_id"] and x["category"]}, key=lambda t: t[1].lower()),
        "subcategories": sorted({(x["subcategory_id"], x["subcategory"], x["category_id"]) for x in rows if x["subcategory_id"] and x["subcategory"]}, key=lambda t: t[1].lower()),
        "first_date": min((x["date"] for x in rows if x["date"]), default=None),
        "last_date": max((x["date"] for x in rows if x["date"]), default=None),
    }
    options["categories"] = [{"id": i, "name": n} for i, n in options["categories"]]
    options["subcategories"] = [{"id": i, "name": n, "category_id": c} for i, n, c in options["subcategories"]]

    start, end = _bounds(f)

    def matches(x, lo=None, hi=None):
        if f["exam_id"] and x["exam_id"] != f["exam_id"]:
            return False
        if f["category_id"] and x["category_id"] != f["category_id"]:
            return False
        if f["subcategory_id"] and x["subcategory_id"] != f["subcategory_id"]:
            return False
        if f["kind"] == "scheduled" and not x["scheduled"]:
            return False
        if f["kind"] == "regular" and x["scheduled"]:
            return False
        if lo and (not x["at"] or x["at"] < lo):
            return False
        if hi and (not x["at"] or x["at"] >= hi):
            return False
        return True

    sel = sorted((x for x in rows if matches(x, start, end)), key=lambda x: (x["at"] or datetime.min, x["result_id"]))

    # Previous period of the same length, only when a full range was chosen.
    previous = None
    if start and end:
        span = end - start
        prev = [x for x in rows if matches(x, start - span, start)]
        previous = {"attempts": len(prev), "average": _avg([x["percentage"] for x in prev])}

    out = {
        "filters": f, "options": options, "hidden_results": hidden, "total_visible_attempts": len(rows),
        "attempts": len(sel), "previous_period": previous,
    }
    if not sel:
        out.update(summary=None, trend=[], grades=[], by_category=[], by_subcategory=[], by_exam=[], by_question_type=[], recent=[], highlights=[])
        return out

    pct = [x["percentage"] for x in sel]
    c, i, u = sum(x["correct"] for x in sel), sum(x["incorrect"] for x in sel), sum(x["unanswered"] for x in sel)
    total_q = sum(x["total"] for x in sel)
    timed = [x["minutes"] for x in sel if x["minutes"]]
    improvement = None
    if len(sel) >= 2:
        recent = sel[-3:]
        earlier = sel[:-3] or sel[:1]
        improvement = round(sum(x["percentage"] for x in recent) / len(recent) - sum(x["percentage"] for x in earlier) / len(earlier), 1)
    out["summary"] = {
        "attempts": len(sel), "exams": len({x["exam_id"] for x in sel}),
        "average": _avg(pct), "best": max(pct), "lowest": min(pct),
        "score": round(sum(x["score"] for x in sel), 1), "max_score": round(sum(x["max_score"] for x in sel), 1),
        "correct": c, "incorrect": i, "unanswered": u, "questions": total_q,
        "accuracy": round(c / (c + i) * 100, 1) if (c + i) else None,
        "attempt_rate": round((c + i) / total_q * 100, 1) if total_q else None,
        "avg_minutes": _avg(timed), "improvement": improvement,
    }

    out["trend"] = [{"result_id": x["result_id"], "exam_id": x["exam_id"], "exam": x["exam"], "date": x["date"], "label": x["label"],
                     "percentage": x["percentage"], "score": x["score"], "max_score": x["max_score"], "grade": x["grade"],
                     "correct": x["correct"], "incorrect": x["incorrect"], "unanswered": x["unanswered"],
                     "minutes": x["minutes"]} for x in sel]

    grades: Dict[str, int] = {}
    for x in sel:
        grades[x["grade"]] = grades.get(x["grade"], 0) + 1
    out["grades"] = [{"grade": g, "count": n, "percentage": round(n / len(sel) * 100, 1)}
                     for g, n in sorted(grades.items(), key=lambda t: t[0])]

    def group(key_id, key_name):
        g: Dict = {}
        for x in sel:
            if not x[key_id]:
                continue
            e = g.setdefault(x[key_id], {"id": x[key_id], "name": x[key_name], "pcts": [], "correct": 0, "incorrect": 0})
            e["pcts"].append(x["percentage"])
            e["correct"] += x["correct"]
            e["incorrect"] += x["incorrect"]
        res = [{"id": e["id"], "name": e["name"], "attempts": len(e["pcts"]), "average": _avg(e["pcts"]), "best": max(e["pcts"]),
                "accuracy": round(e["correct"] / (e["correct"] + e["incorrect"]) * 100, 1) if (e["correct"] + e["incorrect"]) else None}
               for e in g.values()]
        return sorted(res, key=lambda e: (-e["attempts"], e["name"].lower()))

    out["by_category"] = group("category_id", "category")
    out["by_subcategory"] = group("subcategory_id", "subcategory")

    ex: Dict = {}
    for x in sel:
        e = ex.setdefault(x["exam_id"], {"id": x["exam_id"], "name": x["exam"], "rows": []})
        e["rows"].append(x)
    by_exam = []
    for e in ex.values():
        p = [r["percentage"] for r in e["rows"]]
        mins = [r["minutes"] for r in e["rows"] if r["minutes"]]
        by_exam.append({"id": e["id"], "name": e["name"], "attempts": len(p), "average": _avg(p), "best": max(p), "first": p[0],
                        "latest": p[-1], "change": round(p[-1] - p[0], 1) if len(p) > 1 else None, "avg_minutes": _avg(mins)})
    out["by_exam"] = sorted(by_exam, key=lambda e: (-e["attempts"], e["name"].lower()))[:12]

    qtypes = []
    try:
        stats = stats_loader([x["result_id"] for x in sel])
    except Exception as e:                                   # a failing side query must not take the dashboard down
        print(f"[student_analytics] response stats unavailable: {type(e).__name__}: {e}")
        stats = []
    agg: Dict = {}
    for s in stats:
        t = agg.setdefault(str(s.get("question_type") or "MCQ"), {"questions": 0, "attempted": 0, "correct": 0, "marks": 0.0})
        t["questions"] += int(s.get("questions") or 0)
        t["attempted"] += int(s.get("attempted") or 0)
        t["correct"] += int(s.get("correct") or 0)
        t["marks"] += _f(s.get("marks"))
    for name, t in sorted(agg.items()):
        qtypes.append({"type": name, **t, "marks": round(t["marks"], 1),
                       "accuracy": round(t["correct"] / t["attempted"] * 100, 1) if t["attempted"] else None})
    out["by_question_type"] = qtypes

    out["recent"] = [{"result_id": x["result_id"], "exam_id": x["exam_id"], "exam": x["exam"], "label": x["label"],
                      "score": x["score"], "max_score": x["max_score"], "percentage": x["percentage"], "grade": x["grade"],
                      "minutes": x["minutes"]} for x in reversed(sel[-10:])]

    # Plain facts read off the numbers above; nothing is inferred beyond them.
    hl = []
    ranked = [g for g in out["by_category"] if g["attempts"] >= 1]
    if len(ranked) >= 2:
        best, worst = max(ranked, key=lambda g: g["average"] or 0), min(ranked, key=lambda g: g["average"] or 0)
        if best["id"] != worst["id"]:
            hl.append(f"Highest average by category: {best['name']} ({best['average']}%). Lowest: {worst['name']} ({worst['average']}%).")
    if improvement is not None and len(sel) >= 4:
        hl.append(f"Your last {min(3, len(sel))} attempts average {'+' if improvement >= 0 else ''}{improvement} points {'above' if improvement >= 0 else 'below'} your earlier attempts.")
    if previous and previous["average"] is not None and out["summary"]["average"] is not None:
        d = round(out["summary"]["average"] - previous["average"], 1)
        hl.append(f"Average {'+' if d >= 0 else ''}{d} points compared with the previous period of the same length ({previous['attempts']} attempt(s)).")
    out["highlights"] = hl
    return out
