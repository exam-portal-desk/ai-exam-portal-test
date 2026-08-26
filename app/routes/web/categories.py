from flask import Blueprint, render_template, redirect, url_for, request, session
from app.middleware.session_guard import require_user_role
from app.db.categories import get_category_by_id, get_categories_page, get_categories_sample
from app.db.subcategories import get_subcategory_by_id, get_subcategories_page, get_subcategories_sample
from app.db.users import get_view_prefs
from app.services.image_storage_service import resolve_category_image_url
from app.services.dashboard_service import should_auto_show_updates

categories_bp = Blueprint("categories", __name__)

PORTAL_PAGE_SIZE = 24


@categories_bp.route("/select-category")
@require_user_role
def select_category():
    # Cheap LIMIT-2 probe (no full-table fetch) just to decide whether
    # there's only one category to skip the picker for.
    cats_probe = get_categories_sample(2)
    if len(cats_probe) == 1:
        return redirect(url_for("categories.set_category", id=cats_probe[0]["id"]))

    page_data = get_categories_page(page=1, per_page=PORTAL_PAGE_SIZE)
    for cat in page_data["categories"]:
        cat["image_url"] = resolve_category_image_url(cat)
    return render_template(
        "categories.html",
        categories=page_data["categories"],
        categories_total=page_data["total"], categories_total_pages=page_data["total_pages"],
        categories_per_page=PORTAL_PAGE_SIZE,
        view_mode=get_view_prefs(session["user_id"]).get("categories", "grid"),
        auto_show_updates=should_auto_show_updates(),
    )


@categories_bp.route("/set-category")
@require_user_role
def set_category():
    cat_id = request.args.get("id", type=int)
    if not cat_id or not get_category_by_id(cat_id):
        return redirect(url_for("categories.select_category"))

    session["selected_category_id"] = cat_id
    session.pop("selected_subcategory_id", None)
    session.modified = True

    subcats_probe = get_subcategories_sample(cat_id, 2)
    if len(subcats_probe) <= 1:
        # 0 subcategories yet (nothing to browse) or exactly 1 (nothing to
        # choose) — skip the picker, same convention as the category picker
        # itself (select_category skips straight through when there's only
        # one category).
        if subcats_probe:
            session["selected_subcategory_id"] = subcats_probe[0]["id"]
            session.modified = True
        return redirect(url_for("dashboard.dashboard"))

    return redirect(url_for("categories.select_subcategory"))


@categories_bp.route("/select-subcategory")
@require_user_role
def select_subcategory():
    cat_id = session.get("selected_category_id")
    cat = get_category_by_id(cat_id) if cat_id else None
    if not cat:
        return redirect(url_for("categories.select_category"))

    # Cheap probe (same rationale as select_category above) to decide
    # whether there's anything to pick from at all.
    subs_probe = get_subcategories_sample(cat_id, 2)
    if len(subs_probe) <= 1:
        if subs_probe:
            session["selected_subcategory_id"] = subs_probe[0]["id"]
            session.modified = True
        return redirect(url_for("dashboard.dashboard"))

    page_data = get_subcategories_page(cat_id, page=1, per_page=PORTAL_PAGE_SIZE)
    return render_template(
        "subcategories.html", category=cat, subcategories=page_data["subcategories"],
        subcategories_total=page_data["total"], subcategories_total_pages=page_data["total_pages"],
        subcategories_per_page=PORTAL_PAGE_SIZE,
        view_mode=get_view_prefs(session["user_id"]).get("subcategories", "grid"),
        auto_show_updates=should_auto_show_updates(),
    )


@categories_bp.route("/set-subcategory")
@require_user_role
def set_subcategory():
    subcat_id = request.args.get("id", type=int)
    subcat = get_subcategory_by_id(subcat_id) if subcat_id else None
    if not subcat or subcat["category_id"] != session.get("selected_category_id"):
        return redirect(url_for("categories.select_category"))

    session["selected_subcategory_id"] = subcat_id
    session.modified = True
    return redirect(url_for("dashboard.dashboard"))
