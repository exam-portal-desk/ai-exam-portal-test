"""
app/routes/web/admin/results.py
Admin analytics pages, result/response popups, and PDF download — all
HTML/binary. The 2 JSON API routes that used to live alongside these in
app/routes/admin/results.py now live in
app/routes/api/v01/admin/analytics.py.
"""

from flask import render_template, request, abort, send_file

from app.routes.web.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_all_exams, get_exam_by_id
from app.db.users import get_user_by_id, get_users_by_ids, get_all_users
from app.db.results import get_result_by_id, get_responses_by_result
from app.db.questions import get_questions_by_exam
from app.db import fetch_one, fetch_all

DELETED_USER_LABEL = "Deleted User"


def _display_username(user: dict) -> str:
    """Username/handle to show for a result/response row's owner. Never
    falls back to a raw id (e.g. "User 4") — per the Account & Data
    Deletion Policy, private exam data no longer exists once its owner is
    deleted, so this only triggers for genuinely missing/legacy user
    records, and even then must read as a clean deleted-user label."""
    return user.get("username") or DELETED_USER_LABEL


def _display_full_name(user: dict) -> str:
    return user.get("full_name") or user.get("username") or DELETED_USER_LABEL


# ── Analytics dashboard ───────────────────────────────────────────────────

@admin_bp.route("/users-analytics")
@require_admin_role
def users_analytics():
    exams = get_all_exams()
    exams_list = [{"id": int(e["id"]), "name": e.get("name","")} for e in exams]

    # One round trip (scalar subqueries) instead of 4 sequential COUNT
    # queries (flagged in the architecture audit).
    counts = fetch_one(
        "SELECT (SELECT COUNT(*) FROM users) AS total_users, "
        "(SELECT COUNT(*) FROM exams) AS total_exams, "
        "(SELECT COUNT(*) FROM results) AS total_results, "
        "(SELECT COUNT(*) FROM responses) AS total_responses"
    )

    # First page of results
    page_data = fetch_all("SELECT * FROM results ORDER BY completed_at DESC LIMIT 20 OFFSET 0")
    sid_set = {str(r.get("student_id")) for r in page_data}
    eid_set = {str(r.get("exam_id")) for r in page_data}
    um = get_users_by_ids([int(x) for x in sid_set if x])
    em = {str(e["id"]): e for e in fetch_all("SELECT id,name FROM exams WHERE id = ANY(%s)", ([int(x) for x in eid_set if x],))}

    results_page = []
    for r in page_data:
        u = um.get(str(r.get("student_id","")),{}); e = em.get(str(r.get("exam_id","")),{})
        results_page.append({
            "id":         int(r.get("id",0)),
            "username":   _display_username(u),
            "full_name":  _display_full_name(u),
            "exam_id":    int(r.get("exam_id",0)),
            "exam_name":  e.get("name","Unknown"),
            "score":      r.get("score",0),
            "max_score":  r.get("max_score",0),
            "percentage": float(r.get("percentage",0)),
            "grade":      r.get("grade","N/A"),
            "duration":   f"{r.get('time_taken_minutes',0):.1f} min" if r.get("time_taken_minutes") else "N/A",
            "created_at": r.get("completed_at",""),
        })

    total = counts["total_results"]
    total_pages = max(1,(total+19)//20)
    pagination = {"page":1,"per_page":20,"total":total,"start":1 if results_page else 0,
                  "end":len(results_page),"has_prev":False,"has_next":total_pages>1,
                  "prev_num":None,"next_num":2 if total_pages>1 else None,"total_pages":total_pages}

    def _ip():
        for p in range(1, min(total_pages+1,4)): yield p
    pagination["iter_pages"] = _ip

    filter_users = [{"id":int(u["id"]),"username":u.get("username",""),"full_name":u.get("full_name","")}
                    for u in get_all_users()]

    return render_template("admin/users_analytics.html", stats=counts, exams=exams_list,
                           results=results_page, pagination=pagination, filter_users=filter_users)


@admin_bp.route("/users-analytics/results")
@require_admin_role
def users_analytics_results():
    """
    AJAX-paginated results with working date filter.
    Param: partial=1 returns only the table fragment (no full page).
    Date params: dateFrom / dateTo in YYYY-MM-DD (native <input type=date> format).
    """
    user_filter = request.args.get("user",     "").strip()
    exam_filter = request.args.get("exam",     "").strip()
    date_from   = request.args.get("dateFrom", "").strip()
    date_to     = request.args.get("dateTo",   "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    per_page  = 20
    start_idx = (page - 1) * per_page

    try:
        # ── Shared WHERE clause ──────────────────────────────────────
        where, params = [], []
        if user_filter: where.append("student_id=%s"); params.append(user_filter)
        if exam_filter: where.append("exam_id=%s");     params.append(exam_filter)
        if date_from:   where.append("completed_at >= %s"); params.append(date_from)
        if date_to:     where.append("completed_at <= %s"); params.append(date_to + "T23:59:59")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        # ── Count query ───────────────────────────────────────────────
        total_results = fetch_one(f"SELECT COUNT(*) AS count FROM results {where_sql}", params)["count"]

        # ── Data query ────────────────────────────────────────────────
        page_results = fetch_all(
            f"SELECT * FROM results {where_sql} ORDER BY completed_at DESC LIMIT %s OFFSET %s",
            params + [per_page, start_idx],
        )

        # ── Lookup maps (only IDs on this page) ───────────────────────
        sid_set = {str(r.get("student_id")) for r in page_results}
        eid_set = {str(r.get("exam_id"))    for r in page_results}
        users_map, exams_map = {}, {}

        if sid_set:
            ur = fetch_all(
                "SELECT id, username, full_name FROM users WHERE id = ANY(%s)",
                ([int(x) for x in sid_set if x],),
            )
            users_map = {str(u["id"]): u for u in ur}

        if eid_set:
            er = fetch_all(
                "SELECT id, name FROM exams WHERE id = ANY(%s)",
                ([int(x) for x in eid_set if x],),
            )
            exams_map = {str(e["id"]): e for e in er}

        # ── Build result list ─────────────────────────────────────────
        results_list = []
        for r in page_results:
            sid = str(r.get("student_id", ""))
            eid = str(r.get("exam_id",    ""))
            u   = users_map.get(sid, {})
            e   = exams_map.get(eid, {})
            tt  = r.get("time_taken_minutes")
            results_list.append({
                "id":          int(r.get("id", 0)),
                "username":    _display_username(u),
                "full_name":   _display_full_name(u),
                "exam_id":     int(r.get("exam_id", 0)),
                "exam_name":   e.get("name",      "Unknown Exam"),
                "score":       r.get("score",     0),
                "max_score":   r.get("max_score", 0),
                "percentage":  float(r.get("percentage") or 0),
                "grade":       r.get("grade",     "N/A"),
                "duration":    f"{float(tt):.1f} min" if tt else "N/A",
                "created_at":  r.get("completed_at", "N/A"),
            })

        # ── Pagination object ─────────────────────────────────────────
        total_pages = max(1, -(-total_results // per_page))
        end_idx     = start_idx + len(results_list)

        def iter_pages():
            s = max(1,    page - 2)
            e = min(total_pages, page + 2)
            for p in range(s, e + 1):
                yield p

        pagination = {
            "page":       page,
            "per_page":   per_page,
            "total":      total_results,
            "start":      start_idx + 1 if results_list else 0,
            "end":        end_idx,
            "has_prev":   page > 1,
            "has_next":   page < total_pages,
            "prev_num":   page - 1 if page > 1       else None,
            "next_num":   page + 1 if page < total_pages else None,
            "total_pages": total_pages,
            "iter_pages": iter_pages,
        }

        # ── Partial (AJAX) vs full page ───────────────────────────────
        partial = request.args.get("partial", "0") == "1"
        if partial:
            return render_template(
                "admin/users_analytics_results_table.html",
                results=results_list, pagination=pagination
            )

        # Full page (direct navigation) — also load exams for filter dropdown
        exams_all  = get_all_exams()
        exams_list = [{"id": int(e["id"]), "name": e.get("name", "")} for e in exams_all]

        # filter_users is now empty — user search is done via AJAX autocomplete
        return render_template(
            "admin/users_analytics_results.html",
            results=results_list,
            users=[],          # no longer needed — AJAX search replaces it
            exams=exams_list,
            pagination=pagination
        )

    except Exception as e:
        import traceback; traceback.print_exc()
        return render_template(
            "admin/users_analytics_results_table.html",
            results=[], pagination=None
        )


@admin_bp.route("/users-analytics/analytics")
@require_admin_role
def users_analytics_analytics():
    exams = [{"id": int(e["id"]), "name": e.get("name","")} for e in get_all_exams()]
    return render_template("admin/users_analytics_analytics.html", exams=exams)


@admin_bp.route("/users-analytics/view-result/<int:result_id>/<int:exam_id>")
@require_admin_role
def users_analytics_view_result(result_id, exam_id):
    result = get_result_by_id(result_id)
    if not result: abort(404)
    user  = get_user_by_id(result.get("student_id")) or {"id":result.get("student_id"),"username":DELETED_USER_LABEL,"full_name":DELETED_USER_LABEL,"email":""}
    exam  = get_exam_by_id(exam_id) or {"id":exam_id,"name":"Unknown Exam"}
    resps = get_responses_by_result(result_id)
    norm  = {k: (int(result.get(k,0)) if k in ("id","student_id","total_questions","correct_answers","incorrect_answers","unanswered_questions")
                 else float(result.get(k,0)) if k in ("score","max_score","percentage","time_taken_minutes")
                 else result.get(k,"")) for k in result}
    norm["attempted_questions"] = int(result.get("total_questions",0)) - int(result.get("unanswered_questions",0))
    return render_template("admin/view_result_popup.html", result=norm, user=user, exam=exam, responses=resps)


@admin_bp.route("/users-analytics/view-responses/<int:result_id>/<int:exam_id>")
@require_admin_role
def users_analytics_view_responses(result_id, exam_id):
    import json as _json
    from app.services.image_storage_service import resolve_question_image_url as _get_img_url

    result = get_result_by_id(result_id)
    if not result: abort(404)
    user  = get_user_by_id(result.get("student_id")) or {"id": result.get("student_id"), "username": DELETED_USER_LABEL, "full_name": DELETED_USER_LABEL}
    exam  = get_exam_by_id(exam_id) or {"id": exam_id, "name": "Unknown Exam"}
    resps = get_responses_by_result(result_id)
    qs    = get_questions_by_exam(exam_id)
    qmap  = {str(q["id"]): q for q in qs}

    def _parse_ans(raw, qtype):
        if not raw or raw in ("None", "nan", ""):
            return None
        try:
            if qtype == "MSQ":
                return _json.loads(raw) if raw.startswith("[") else [x.strip() for x in raw.split(",")]
        except Exception:
            pass
        return raw

    # ── Bulk-resolve image URLs (one API call per unique image path) ──
    unique_paths = set()
    for r in resps:
        qid = str(r.get("question_id", ""))
        q   = qmap.get(qid, {})
        ip  = q.get("image_path", "")
        if ip and str(ip).strip() not in ("", "nan", "None"):
            unique_paths.add(str(ip).strip())

    image_url_map = {}  # image_path -> (has_img, img_url)
    for path in unique_paths:
        has_img, img_url = _get_img_url(path)
        image_url_map[path] = (has_img, img_url)

    norm = []
    for r in resps:
        qid   = str(r.get("question_id", ""))
        q     = qmap.get(qid, {})
        qtype = str(r.get("question_type", q.get("question_type", "MCQ")))
        ga    = str(r.get("given_answer", "") or "")
        ca    = str(r.get("correct_answer", "") or "")

        ia = r.get("is_attempted", False)
        if isinstance(ia, str): ia = ia.lower() in ("true", "1", "yes")
        ic = r.get("is_correct", False)
        if isinstance(ic, str): ic = ic.lower() in ("true", "1", "yes")

        given_parsed   = _parse_ans(ga, qtype) or "Not Answered"
        correct_parsed = _parse_ans(ca, qtype) or "N/A"

        if not ia or ga in ("", "None", "nan"):
            given_parsed = "Not Answered"

        status = "unanswered" if (not ia or given_parsed == "Not Answered") else "correct" if ic else "incorrect"

        # Resolve image URL (from pre-built bulk map)
        image_path = q.get("image_path", "")
        if image_path and str(image_path).strip() not in ("", "nan", "None"):
            has_img, img_url = image_url_map.get(str(image_path).strip(), (False, None))
        else:
            has_img, img_url = False, None

        q["has_image"] = has_img
        q["image_url"] = img_url

        norm.append({
            "question_id":    qid,
            "question":       q,           # full question object (options, image, explanation etc.)
            "question_type":  qtype,
            "given_answer":   given_parsed,
            "correct_answer": correct_parsed,
            "is_attempted":   ia,
            "is_correct":     ic,
            "status":         status,
            "marks_obtained": float(r.get("marks_obtained", 0) or 0),
        })

    return render_template("admin/view_responses_popup.html", result=result, user=user, exam=exam, responses=norm)


@admin_bp.route("/users-analytics/download-result/<int:result_id>")
@require_admin_role
def users_analytics_download_result(result_id):
    from io import BytesIO
    from app.services.pdf_service import build_student_response_pdf

    result = get_result_by_id(result_id)
    if not result: abort(404)

    user  = get_user_by_id(result.get("student_id")) or {}
    exam  = get_exam_by_id(result.get("exam_id")) or {}
    resps = get_responses_by_result(result_id)
    qs    = get_questions_by_exam(result.get("exam_id"))
    qmap  = {int(q["id"]): q for q in qs}

    pdf = build_student_response_pdf(
        result=result,
        exam=exam,
        responses=resps,
        questions_map=qmap,
        student_name=_display_full_name(user),
        username=user.get("username","student"),
    )

    username  = user.get("username","student")
    exam_name = exam.get("name","exam").replace(" ","_")

    return send_file(BytesIO(pdf), as_attachment=True,
                     download_name=f"{exam_name}_{username}_result_{result_id}.pdf",
                     mimetype="application/pdf")
