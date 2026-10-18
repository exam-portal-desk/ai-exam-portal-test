"""
app/services/advanced_filter.py
The one Advanced Filter engine (validation + evaluation) behind every "Filters" panel: Manage Exams (admin) and Results
History (student). A page supplies its own FIELDS spec and a `getter(row, field)`; nothing here knows about exams or results.

Wire format (query param `adv`, JSON):
    {"match": "all" | "any", "conditions": [{"field", "op", "value", "value2"}]}

Every condition is validated against the page's FIELDS before use (unknown field / operator / malformed value is rejected,
never ignored), and values are only ever compared in Python, never put into SQL.

getter(row, field) returns the comparable value: str (text, enum), int or None (number, entity), "YYYY-MM-DD" or None
(date), "HH:MM" or None (time). It may return NO_MATCH to say "this row can never satisfy a condition on this field" for
any operator (used to keep hidden data hidden: a negative operator must not make a row match either).
"""

import json
from datetime import datetime
from typing import Callable, Dict, List, Optional

MAX_CONDITIONS = 12
_MAX_TEXT = 200

TEXT_OPS = {"contains", "not_contains", "equals", "starts_with", "ends_with"}
NUM_OPS = {"gt", "lt", "eq", "between"}
DATE_OPS = {"on", "before", "after", "between"}
TIME_OPS = {"before", "after", "at", "between"}
ENUM_OPS = {"is", "is_not"}
OPS_BY_KIND = {"text": TEXT_OPS, "number": NUM_OPS, "date": DATE_OPS, "time": TIME_OPS, "enum": ENUM_OPS, "entity": ENUM_OPS}

NO_MATCH = object()


class FilterError(ValueError):
    pass


def _parse_date(v) -> str:
    try:
        return datetime.strptime(str(v).strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise FilterError("Dates must be YYYY-MM-DD.")


def _parse_time(v) -> str:
    try:
        return datetime.strptime(str(v).strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        raise FilterError("Times must be HH:MM.")


def _parse_int(v) -> int:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        raise FilterError("Numbers must be whole numbers.")
    if n < 0 or n > 10_000_000:
        raise FilterError("Number is out of range.")
    return n


def _clean_condition(raw, fields: Dict) -> Dict:
    if not isinstance(raw, dict):
        raise FilterError("Invalid filter condition.")
    field = raw.get("field")
    spec = fields.get(field)
    if not spec:
        raise FilterError("Unknown filter field.")
    kind = spec["kind"]
    op = raw.get("op")
    if op not in OPS_BY_KIND[kind]:
        raise FilterError("Unsupported operator for this field.")
    cond = {"field": field, "op": op, "value": None, "value2": None}
    v, v2 = raw.get("value"), raw.get("value2")
    parse = {"number": _parse_int, "date": _parse_date, "time": _parse_time}.get(kind)
    if kind == "text":
        text = str(v or "").strip()[:_MAX_TEXT]
        if not text:
            raise FilterError("Enter some text to filter by.")
        cond["value"] = text
    elif parse:
        cond["value"] = parse(v)
        if op == "between":
            cond["value2"] = parse(v2)
            if cond["value"] > cond["value2"]:
                cond["value"], cond["value2"] = cond["value2"], cond["value"]
    elif kind == "enum":
        if str(v) not in spec["values"]:
            raise FilterError("Unsupported value for this field.")
        cond["value"] = str(v)
    else:  # entity
        cond["value"] = _parse_int(v)
    return cond


def parse(raw, fields: Dict, max_conditions: int = MAX_CONDITIONS) -> Optional[Dict]:
    """Validate the client JSON; {"match", "conditions"} or None when there is nothing to filter by. Raises FilterError."""
    if raw is None or raw == "":
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        raise FilterError("Invalid filter.")
    if not isinstance(data, dict):
        raise FilterError("Invalid filter.")
    conds = data.get("conditions") or []
    if not isinstance(conds, list):
        raise FilterError("Invalid filter.")
    if not conds:
        return None
    if len(conds) > max_conditions:
        raise FilterError(f"At most {max_conditions} conditions are allowed.")
    return {"match": "any" if data.get("match") == "any" else "all", "conditions": [_clean_condition(c, fields) for c in conds]}


def _compare(op: str, actual, v, v2) -> bool:
    if actual is None:
        return False
    if op in ("gt", "after"):
        return actual > v
    if op in ("lt", "before"):
        return actual < v
    if op in ("eq", "on", "at"):
        return actual == v
    if op == "between":
        return v <= actual <= v2
    return False


def _matches(row, cond: Dict, fields: Dict, getter: Callable) -> bool:
    field, op, v, v2 = cond["field"], cond["op"], cond["value"], cond["value2"]
    kind = fields[field]["kind"]
    actual = getter(row, field)
    if actual is NO_MATCH:
        return False
    if kind == "text":
        name, needle = str(actual or "").lower(), v.lower()
        if op == "contains":
            return all(w in name for w in needle.split())
        if op == "not_contains":
            return not all(w in name for w in needle.split())
        if op == "equals":
            return name == needle
        if op == "starts_with":
            return name.startswith(needle)
        return name.endswith(needle)
    if kind in ("number", "date", "time"):
        return _compare(op, actual, v, v2)
    hit = actual is not None and (int(actual) == v if kind == "entity" else actual == v)
    return hit if op == "is" else not hit


def apply(rows: List, adv: Optional[Dict], fields: Dict, getter: Callable) -> List:
    if not adv:
        return rows
    check = any if adv["match"] == "any" else all
    return [r for r in rows if check(_matches(r, c, fields, getter) for c in adv["conditions"])]
