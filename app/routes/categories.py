from flask import Blueprint, render_template, redirect, url_for, request, session
from app.middleware.session_guard import require_user_role
from app.db.categories import get_all_categories, get_category_by_id

categories_bp = Blueprint("categories", __name__)


@categories_bp.route("/select-category")
@require_user_role
def select_category():
    cats = get_all_categories()
    return render_template("categories.html", categories=cats)


@categories_bp.route("/set-category")
@require_user_role
def set_category():
    cat_id = request.args.get("id", type=int)
    if cat_id:
        session["selected_category_id"] = cat_id
        session.modified = True
    return redirect(url_for("dashboard.dashboard"))