"""
app/services/results_history_service.py
Server-side filtering, sorting and paging of the signed-in student's Results History, on the shared Advanced Filter engine
(app/services/advanced_filter.py).

Isolation: everything starts from get_results_by_user(user_id) with the id taken from the session. Filters only narrow that
set, and are evaluated here, over ALL of the student's results, never just the page on screen.

Hidden data stays hidden: a result the student may not see yet (manual / delayed release) has no score, grade, percentage,
counts or time for a filter to look at, so any condition on those fields never matches such a row, whatever the operator.
Only the exam, category, date and status fields apply to it.

There is no per-question subject in the data model, so there is no subject filter.
"""

from datetime import datetime
from typing import Dict, List, Optional

from app.db.results import get_results_by_user
from app.services import advanced_filter as engine
from app.services.advanced_filter import FilterError, NO_MATCH  # noqa: F401
from app.services.student_analytics_service import _naive_utc
from app.utils.datetime_service import to_app_tz

SORTS = ("newest", "oldest", "score_high", "score_low", "exam")
PAGE_SIZES = (10, 25, 50, 100)
_RESULT_ONLY = {"grade", "score", "percentage", "correct", "incorrect", "unanswered", "time_taken"}

BASE_FIELDS: Dict[str, Dict] = {
    "exam_name":  {"kind": "text"},
    "exam":       {"kind": "entity"},
    "category":   {"kind": "entity"},
    "subcategory": {"kind": "entity"},
    "date":       {"kind": "date"},
    "time":       {"kind": "time"},
    "status":     {"kind": "enum", "values": ("released", "pending")},
    "kind":       {"kind": "enum", "values": ("scheduled", "regular")},
    "grade":      {"kind": "enum", "values": ()},
    "score":      {"kind": "number"},
    "percentage": {"kind": "number"},
    "correct":    {"kind": "number"},
    "incorrect":  {"kind": "number"},
    "unanswered": {"kind": "number"},
    "time_taken": {"kind": "number"},
}


def fields_for(grades) -> Dict[str, Dict]:
    f = {k: dict(v) for k, v in BASE_FIELDS.items()}
    f["grade"]["values"] = tuple(sorted(grades))
    return f


def _load(user_id: int):
    from app.db.exams import get_exams_by_ids_full
    from app.db.categories import get_all_categories
    from app.db.subcategories import get_all_subcategories
    results = get_results_by_user(user_id)
    exams = get_exams_by_ids_full(sorted({int(r["exam_id"]) for r in results if r.get("exam_id")}))
    cats = {int(c["id"]): c.get("name", "") for c in get_all_categories()}
    subs = {int(s["id"]): s.get("name", "") for s in get_all_subcategories()}
    return results, exams, cats, subs


def _row(r: Dict, exam: Dict, cats, subs, visible: bool, reason: str) -> Dict:
    done = _naive_utc(r.get("completed_at"))
    local = to_app_tz(done) if done else None
    eid = int(r.get("exam_id") or 0)
    cid, sid = exam.get("category_id"), exam.get("subcategory_id")
    return {
        "id": int(r.get("id") or 0), "exam_id": eid, "exam_name": exam.get("name") or f"Exam {eid}",
        "completed_at": r.get("completed_at") or "", "_at": done,
        "_date": local.strftime("%Y-%m-%d") if local else None, "_time": local.strftime("%H:%M") if local else None,
        "category_id": cid, "subcategory_id": sid,
        "score": r.get("score", 0), "max_score": r.get("max_score", 0),
        "percentage": round(float(r.get("percentage") or 0), 2), "grade": r.get("grade") or "N/A",
        "time_taken_minutes": r.get("time_taken_minutes", 0),
        "correct_answers": int(r.get("correct_answers") or 0), "incorrect_answers": int(r.get("incorrect_answers") or 0),
        "unanswered_questions": int(r.get("unanswered_questions") or 0),
        "result_visible": visible, "pending_reason": reason, "is_scheduled": bool(exam.get("scheduled_mode")),
    }


def _value(row: Dict, field: str):
    if field in _RESULT_ONLY and not row["result_visible"]:
        return NO_MATCH
    return {
        "exam_name": lambda: row["exam_name"], "exam": lambda: row["exam_id"], "category": lambda: row["category_id"],
        "subcategory": lambda: row["subcategory_id"], "date": lambda: row["_date"], "time": lambda: row["_time"],
        "status": lambda: "released" if row["result_visible"] else "pending",
        "kind": lambda: "scheduled" if row["is_scheduled"] else "regular", "grade": lambda: str(row["grade"]),
        "score": lambda: row["score"], "percentage": lambda: row["percentage"], "correct": lambda: row["correct_answers"],
        "incorrect": lambda: row["incorrect_answers"], "unanswered": lambda: row["unanswered_questions"],
        "time_taken": lambda: row["time_taken_minutes"],
    }[field]()


def _sort(rows: List[Dict], sort: str) -> List[Dict]:
    if sort == "oldest":
        return sorted(rows, key=lambda r: (r["_at"] or datetime.min, r["id"]))
    if sort in ("score_high", "score_low"):
        # pending results have no visible score: they always go last, so the order never reveals one
        shown = sorted([r for r in rows if r["result_visible"]], key=lambda r: (r["percentage"], r["id"]), reverse=sort == "score_high")
        return shown + [r for r in rows if not r["result_visible"]]
    newest = sorted(rows, key=lambda r: (r["_at"] or datetime.min, r["id"]), reverse=True)
    if sort == "exam":
        return sorted(newest, key=lambda r: r["exam_name"].lower())          # stable: newest first within one exam
    return newest


def build_rows(user_id: int, loader=None):
    from app.services.result_service import can_user_see_result
    results, exams, cats, subs = (loader or _load)(user_id)
    out = []
    for r in results:
        if r.get("student_id") not in (None, user_id):
            continue
        exam = exams.get(str(r.get("exam_id"))) or exams.get(r.get("exam_id")) or {}
        visible, reason = can_user_see_result(exam, r)
        out.append(_row(r, exam, cats, subs, visible, reason))
    return out, cats, subs


def options_for(rows: List[Dict], cats, subs) -> Dict:
    exams = {r["exam_id"]: r["exam_name"] for r in rows}
    cat_ids = {r["category_id"] for r in rows if r["category_id"]}
    sub_rows = {r["subcategory_id"]: r["category_id"] for r in rows if r["subcategory_id"]}
    return {
        "exams": sorted(([i, n] for i, n in exams.items()), key=lambda t: t[1].lower()),
        "categories": sorted(([int(i), cats.get(int(i), "")] for i in cat_ids if cats.get(int(i))), key=lambda t: t[1].lower()),
        "subcategories": sorted(([int(i), subs.get(int(i), ""), c] for i, c in sub_rows.items() if subs.get(int(i))), key=lambda t: t[1].lower()),
        "grades": sorted({str(r["grade"]) for r in rows if r["result_visible"]}),
    }


def parse_request(args, grades):
    """(adv, sort, page, per_page) from a query string, validated."""
    adv = engine.parse(args.get("adv"), fields_for(grades))
    sort = args.get("sort") or "newest"
    if sort not in SORTS:
        raise FilterError("Sort order is not valid.")
    try:
        page = max(1, int(args.get("page") or 1))
        per_page = int(args.get("per_page") or 10)
    except ValueError:
        raise FilterError("Paging is not valid.")
    if per_page not in PAGE_SIZES:
        raise FilterError("Page size is not valid.")
    return adv, sort, page, per_page


def history_page(user_id: int, args, loader=None) -> Dict:
    rows, cats, subs = build_rows(user_id, loader)
    options = options_for(rows, cats, subs)
    adv, sort, page, per_page = parse_request(args, options["grades"])
    matched = _sort(engine.apply(rows, adv, fields_for(options["grades"]), _value), sort)
    total = len(matched)
    pages = max(1, -(-total // per_page))
    page = min(page, pages)
    return {"results": matched[(page - 1) * per_page: page * per_page], "total": total, "total_all": len(rows), "page": page,
            "per_page": per_page, "total_pages": pages, "sort": sort, "options": options}
