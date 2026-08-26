"""
app/routes/api/v01/portal.py
Student-facing "User Portal" JSON API: paginated categories/subcategories/
exams (backing "Load more" on templates/categories.html, subcategories.html,
dashboard.html) plus the breadcrumb switcher's dropdown data, and the
generic grid/list view-mode preference endpoint. Mirrors the Admin API's
pagination shape (page/per_page/total/total_pages) but scoped to what a
student is allowed to see, and enforced from the session — never from a
client-supplied category/subcategory id — so this can't be used to browse
another portal's data out of the normal selection flow.
"""

from flask import Blueprint, jsonify, request, session, render_template_string

from app.middleware.session_guard import require_user_role
from app.db.categories import get_categories_page, get_all_categories, get_category_by_id
from app.db.subcategories import get_subcategories_page, get_subcategories_by_category
from app.db.exams import get_exams_by_subcategory_page
from app.db.results import get_results_by_user
from app.db.users import set_view_pref
from app.services.image_storage_service import resolve_category_image_url
from app.services.result_service import build_result_map, build_exam_card

portal_bp = Blueprint("portal_api", __name__, url_prefix="/api/v01/portal")

_CARD_TPL = (
    '{% from "_portal_cards.html" import render_category_card, render_subcategory_card %}'
    '{% if kind == "category" %}{% for item in items %}{{ render_category_card(item) }}{% endfor %}'
    '{% else %}{% for item in items %}{{ render_subcategory_card(item) }}{% endfor %}{% endif %}'
)

_EXAM_CARD_TPL = (
    '{% from "_dashboard_exam_cards.html" import render_live_card, render_upcoming_card, render_completed_card %}'
    '{% for e in items %}'
    '{% if status == "ongoing" %}{{ render_live_card(e) }}'
    '{% elif status == "upcoming" %}{{ render_upcoming_card(e) }}'
    '{% else %}{{ render_completed_card(e) }}{% endif %}'
    '{% endfor %}'
)


@portal_bp.route("/categories")
@require_user_role
def api_portal_categories():
    result = get_categories_page(
        search=request.args.get("q", "").strip(),
        page=request.args.get("page", 1),
        per_page=request.args.get("per_page", 24),
    )
    for cat in result["categories"]:
        cat["image_url"] = resolve_category_image_url(cat)
    cards_html = render_template_string(_CARD_TPL, kind="category", items=result["categories"])
    del result["categories"]
    result["cards_html"] = cards_html
    return jsonify(result)


@portal_bp.route("/subcategories")
@require_user_role
def api_portal_subcategories():
    category_id = session.get("selected_category_id")
    if not category_id:
        return jsonify({"subcategories": [], "cards_html": "", "total": 0, "page": 1, "per_page": 24, "total_pages": 1})

    result = get_subcategories_page(
        category_id,
        search=request.args.get("q", "").strip(),
        page=request.args.get("page", 1),
        per_page=request.args.get("per_page", 24),
    )
    cards_html = render_template_string(_CARD_TPL, kind="subcategory", items=result["subcategories"])
    del result["subcategories"]
    result["cards_html"] = cards_html
    return jsonify(result)


@portal_bp.route("/exams")
@require_user_role
def api_portal_exams():
    subcategory_id = session.get("selected_subcategory_id")
    status = request.args.get("status", "").strip()
    if not subcategory_id or status not in ("ongoing", "upcoming", "completed"):
        return jsonify({"cards_html": "", "total": 0, "page": 1, "per_page": 12, "total_pages": 1})

    result = get_exams_by_subcategory_page(
        subcategory_id, status=status,
        search=request.args.get("q", "").strip(),
        page=request.args.get("page", 1),
        per_page=request.args.get("per_page", 12),
    )

    result_map = build_result_map(get_results_by_user(session["user_id"])) if status == "completed" else None
    cards = [build_exam_card(e, result_map) for e in result["exams"]]
    cards_html = render_template_string(_EXAM_CARD_TPL, status=status, items=cards)
    del result["exams"]
    result["cards_html"] = cards_html
    return jsonify(result)


@portal_bp.route("/breadcrumb-options")
@require_user_role
def api_breadcrumb_options():
    """Feeds the compact Category/Subcategory switcher dropdown — capped
    so a very large catalog can't balloon this into a full-table fetch;
    beyond the cap the dropdown just points at the full picker page."""
    cap = 50
    cats = get_all_categories()
    subs = []
    category_id = session.get("selected_category_id")
    if category_id:
        subs = get_subcategories_by_category(category_id)

    return jsonify({
        "categories": [{"id": c["id"], "name": c["name"]} for c in cats[:cap]],
        "categories_truncated": len(cats) > cap,
        "subcategories": [{"id": s["id"], "name": s["name"]} for s in subs[:cap]],
        "subcategories_truncated": len(subs) > cap,
    })


@portal_bp.route("/view-mode", methods=["PATCH"])
@require_user_role
def api_set_view_mode():
    data = request.get_json(silent=True) or {}
    section = str(data.get("section") or "").strip()
    view_mode = data.get("view_mode")
    if not section or view_mode not in ("grid", "list"):
        return jsonify({"success": False, "message": "section and a valid view_mode are required"}), 400

    if not set_view_pref(session["user_id"], section, view_mode):
        return jsonify({"success": False, "message": "Unable to save your view preference. Please try again."}), 500
    return jsonify({"success": True, "section": section, "view_mode": view_mode})
