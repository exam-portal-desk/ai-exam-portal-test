"""
app/services/exam_filters.py
Manage Exams' fields for the shared Advanced Filter engine (app/services/advanced_filter.py): which exam fields can be
filtered on, and how each is read from an exam row. Validation and AND/OR evaluation live in the engine.
"""

from datetime import datetime, timedelta
from typing import Dict, Optional

from app.services import advanced_filter as engine
from app.services.advanced_filter import FilterError, MAX_CONDITIONS  # noqa: F401  (re-exported for callers and tests)

EXAM_STATUSES = ("upcoming", "ongoing", "completed", "cancelled", "draft")
RESULT_MODES = ("instant", "delayed", "manual")

FIELDS: Dict[str, Dict] = {
    "name":            {"kind": "text"},
    "scheduled":       {"kind": "enum", "values": ("scheduled", "not_scheduled")},
    "total_questions": {"kind": "number"},
    "questions_added": {"kind": "number", "needs": "questions_added"},
    "status":          {"kind": "enum", "values": EXAM_STATUSES},
    "category":        {"kind": "entity"},
    "subcategory":     {"kind": "entity"},
    "date":            {"kind": "date"},
    "end_date":        {"kind": "date"},
    "start_time":      {"kind": "time"},
    "duration":        {"kind": "number"},
    "max_attempts":    {"kind": "number"},
    "attempts_made":   {"kind": "number", "needs": "attempts_made"},
    "result_mode":     {"kind": "enum", "values": RESULT_MODES},
    "results_released": {"kind": "enum", "values": ("released", "not_released")},
}


def parse_advanced_filter(raw) -> Optional[Dict]:
    return engine.parse(raw, FIELDS)


def needed_extras(adv: Optional[Dict]) -> set:
    """Which derived per-exam counts the conditions need loaded."""
    return {FIELDS[c["field"]]["needs"] for c in (adv or {}).get("conditions", []) if FIELDS[c["field"]].get("needs")}


def _end_date(row) -> Optional[str]:
    try:
        start = datetime.strptime(f"{row.get('date')} {row.get('start_time')}", "%Y-%m-%d %H:%M")
        return (start + timedelta(minutes=int(row.get("duration") or 0))).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _value(row: Dict, field: str):
    if field == "name":
        return row.get("name")
    if field == "max_attempts":
        return row.get("max_attempts") or None           # 0 / NULL = unlimited, which has no number to compare
    if field in ("total_questions", "duration"):
        return row.get(field) or 0
    if field in ("questions_added", "attempts_made"):
        return int(row.get(field) or 0)
    if field == "date":
        return str(row.get("date") or "") or None
    if field == "end_date":
        return _end_date(row)
    if field == "start_time":
        return str(row.get("start_time") or "") or None
    if field == "category":
        return row.get("category_id")
    if field == "subcategory":
        return row.get("subcategory_id")
    if field == "status":
        return str(row.get("effective_status") or row.get("status") or "").lower()
    if field == "scheduled":
        return "scheduled" if row.get("scheduled_mode") else "not_scheduled"
    if field == "result_mode":
        return str(row.get("result_mode") or "instant").lower()
    return "released" if row.get("results_released") else "not_released"       # results_released


def apply_advanced_filter(rows, adv: Optional[Dict]):
    return engine.apply(rows, adv, FIELDS, _value)
