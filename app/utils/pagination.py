"""
app/utils/pagination.py
Small shared helpers for page-number (LIMIT/OFFSET) pagination, so every
admin list endpoint computes/reports paging the same way instead of each
hand-rolling its own math — same {page, per_page, total, total_pages} shape
already used by api_users_search / api_access_requests.
"""

from typing import Tuple, Dict


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
