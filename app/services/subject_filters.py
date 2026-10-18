"""
app/services/subject_filters.py
Manage Subjects' fields for the shared Advanced Filter engine (app/services/advanced_filter.py). A Subject only has a name and
a creation time, so those are the only fields offered.
"""

from datetime import datetime
from typing import Dict, Optional

from app.services import advanced_filter as engine
from app.services.advanced_filter import FilterError  # noqa: F401
from app.utils.datetime_service import to_app_tz

FIELDS: Dict[str, Dict] = {
    "name": {"kind": "text"},
    "created": {"kind": "date"},
}
SORTS = ("name", "name_desc", "newest", "oldest")


def parse_advanced_filter(raw) -> Optional[Dict]:
    return engine.parse(raw, FIELDS)


def _created(row: Dict) -> Optional[datetime]:
    v = row.get("subject_folder_created_at")
    if not v:
        return None
    try:
        return v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("T", " "))
    except ValueError:
        return None


def _value(row: Dict, field: str):
    if field == "name":
        return row.get("subject_name")
    d = _created(row)
    local = to_app_tz(d) if d else None
    return local.strftime("%Y-%m-%d") if local else None


def apply_advanced_filter(rows, adv):
    return engine.apply(rows, adv, FIELDS, _value)


def sort_rows(rows, sort: str):
    if sort == "name_desc":
        return sorted(rows, key=lambda r: str(r.get("subject_name") or "").lower(), reverse=True)
    if sort in ("newest", "oldest"):
        return sorted(rows, key=lambda r: (_created(r) or datetime.min, r.get("id") or 0), reverse=sort == "newest")
    return sorted(rows, key=lambda r: str(r.get("subject_name") or "").lower())
