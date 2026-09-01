"""
app/utils/pagination.py
Small shared helpers for page-number (LIMIT/OFFSET) pagination, so every
admin list endpoint computes/reports paging the same way instead of each
hand-rolling its own math — same {page, per_page, total, total_pages} shape
already used by api_users_search / api_access_requests.
"""

from typing import Tuple, Dict, List


def attach_row_numbers(rows: List[Dict], page: int, per_page: int) -> List[Dict]:
    """Stamp each row (in place) with a UI-facing 'row_no' — the row's
    absolute position across the whole result set (1, 2, 3... on page 1,
    continuing 21, 22... on page 2 of a 20-per-page list), never the row's
    real DB id. Used by every admin list that currently shows the DB id as
    if it were a serial number; the real id stays untouched on the row for
    anything that still needs it (edit/delete links, etc.)."""
    offset = (page - 1) * per_page
    for i, r in enumerate(rows):
        r["row_no"] = offset + i + 1
    return rows


def paginate_params(page, per_page, max_per_page: int = 200) -> Tuple[int, int, int]:
    """Normalize raw page/per_page query args into (page, per_page, offset)."""
    try:
        page = max(1, int(page or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = max(1, min(max_per_page, int(per_page or 20)))
    except (TypeError, ValueError):
        per_page = 20
    return page, per_page, (page - 1) * per_page


def pagination_meta(total: int, page: int, per_page: int) -> Dict:
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, -(-total // per_page)),
    }
