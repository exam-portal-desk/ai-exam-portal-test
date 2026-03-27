"""
app/routes/admin/results.py
Admin analytics, result viewing, and PDF download.
"""

from datetime import datetime
from flask import render_template, request, jsonify, abort, send_file

from app.routes.admin import admin_bp
from app.middleware.session_guard import require_admin_role
from app.db.exams import get_all_exams, get_exam_by_id
from app.db.users import get_user_by_id, get_users_by_ids, get_all_users
from app.db.results import get_result_by_id,  get_responses_by_result
from app.db.questions import get_questions_by_exam
from app.db import supabase


# ── Analytics dashboard ───────────────────────────────────────────────────

@admin_bp.route("/users-analytics")
@require_admin_role
def users_analytics():
    exams = get_all_exams()
    exams_list = [{"id": int(e["id"]), "name": e.get("name","")} for e in exams]

    counts = {
        "total_users":     supabase.table("users").select("id",count="exact").execute().count or 0,
        "total_exams":     supabase.table("exams").select("id",count="exact").execute().count or 0,
        "total_results":   supabase.table("results").select("id",count="exact").execute().count or 0,
        "total_responses": supabase.table("responses").select("id",count="exact").execute().count or 0,
    }

    # First page of results
    page_res = supabase.table("results").select("*").order("completed_at",desc=True).range(0,19).execute()
    page_data = page_res.data or []
    sid_set = {str(r.get("student_id")) for r in page_data}
    eid_set = {str(r.get("exam_id")) for r in page_data}
    um = get_users_by_ids([int(x) for x in sid_set if x])
    em = {str(e["id"]): e for e in supabase.table("exams").select("id,name").in_("id",list(eid_set)).execute().data or []}

    results_page = []
    for r in page_data:
        u = um.get(str(r.get("student_id","")),{}); e = em.get(str(r.get("exam_id","")),{})
        results_page.append({
            "id":         int(r.get("id",0)),
            "username":   u.get("username","Unknown"),
            "full_name":  u.get("full_name",""),
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
    from app.db.exams import get_all_exams
 
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
        # ── Count query ───────────────────────────────────────────────
        cq = supabase.table("results").select("id", count="exact")
        if user_filter: cq = cq.eq("student_id", user_filter)
        if exam_filter: cq = cq.eq("exam_id",    exam_filter)
        if date_from:   cq = cq.gte("completed_at", date_from)
        if date_to:     cq = cq.lte("completed_at", date_to + "T23:59:59")
        count_r       = cq.execute()
        total_results = count_r.count or 0
 
        # ── Data query ────────────────────────────────────────────────
        dq = (supabase.table("results")
              .select("*")
              .order("completed_at", desc=True)
              .range(start_idx, start_idx + per_page - 1))
        if user_filter: dq = dq.eq("student_id", user_filter)
        if exam_filter: dq = dq.eq("exam_id",    exam_filter)
        if date_from:   dq = dq.gte("completed_at", date_from)
        if date_to:     dq = dq.lte("completed_at", date_to + "T23:59:59")
        data_r      = dq.execute()
        page_results = data_r.data or []
 
        # ── Lookup maps (only IDs on this page) ───────────────────────
        sid_set = {str(r.get("student_id")) for r in page_results}
        eid_set = {str(r.get("exam_id"))    for r in page_results}
        users_map, exams_map = {}, {}
 
        if sid_set:
            ur = (supabase.table("users")
                  .select("id, username, full_name")
                  .in_("id", list(sid_set))
                  .execute())
            users_map = {str(u["id"]): u for u in (ur.data or [])}
 
        if eid_set:
            er = (supabase.table("exams")
                  .select("id, name")
                  .in_("id", list(eid_set))
                  .execute())
            exams_map = {str(e["id"]): e for e in (er.data or [])}
 
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
                "username":    u.get("username",  "Unknown"),
                "full_name":   u.get("full_name", ""),
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


@admin_bp.route("/api/users-analytics/stats")
@require_admin_role
def api_users_analytics_stats():
    """
    4 COUNT-only queries — instant response even with millions of rows.
    Returns compact + exact numbers for stat cards.
    """
    try:
        users_count     = supabase.table("users")    .select("id", count="exact").execute().count or 0
        exams_count     = supabase.table("exams")    .select("id", count="exact").execute().count or 0
        results_count   = supabase.table("results")  .select("id", count="exact").execute().count or 0
        responses_count = supabase.table("responses").select("id", count="exact").execute().count or 0
        return jsonify({
            "total_users":     users_count,
            "total_exams":     exams_count,
            "total_results":   results_count,
            "total_responses": responses_count,
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({
            "total_users":0, "total_exams":0,
            "total_results":0, "total_responses":0
        }), 500


@admin_bp.route("/api/users-analytics/data")
@require_admin_role
def users_analytics_data_api():
    from datetime import datetime as dt, timedelta
    from app.db.results import get_all_results
    from app.db.exams import get_all_exams
    from app.db.users import get_users_by_ids

    time_period = (request.args.get("timePeriod") or "all").lower()
    exam_filter = (request.args.get("exam") or "").strip()
    start_date  = request.args.get("startDate", "")
    end_date    = request.args.get("endDate",   "")
    now         = dt.now()

    try:
        # ── Step 1: Smart date range → fetch only needed slice ────────
        # Instead of fetching all 200K rows, use DB-level date filter
        # and fetch in ONE paginated batch (max ~50K for analytics is fine)

        q = supabase.table("results").select(
            "id, student_id, exam_id, score, max_score, percentage, completed_at"
        ).order("completed_at", desc=True)

        # Apply DB-level filters immediately
        if exam_filter:
            q = q.eq("exam_id", exam_filter)

        if time_period == "today":
            q = q.gte("completed_at", now.strftime("%Y-%m-%d"))
        elif time_period == "week":
            week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
            q = q.gte("completed_at", week_start)
        elif time_period == "month":
            q = q.gte("completed_at", now.strftime("%Y-%m-01"))
        elif time_period == "custom" and start_date and end_date:
            q = q.gte("completed_at", start_date).lte("completed_at", end_date + "T23:59:59")

        # ── Step 2: Paginated fetch with 1000-row chunks ──────────────
        PAGE = 1000
        offset = 0
        all_results = []

        while True:
            batch = q.range(offset, offset + PAGE - 1).execute().data or []
            all_results.extend(batch)
            if len(batch) < PAGE:
                break
            offset += PAGE

            # Safety cap: analytics on 50K rows is already statistically complete
            # Prevents runaway loops on multi-million row tables
            if offset >= 50_000:
                break

        if not all_results:
            return jsonify({
                "summary": {"avgScore":0,"totalAttempts":0,"passRate":0,"activeUsers":0,
                            "scoreChange":0,"attemptsChange":0,"passRateChange":0,"usersChange":0},
                "charts":  {"scoreDistribution":[0,0,0,0],
                            "examPerformance":{"labels":[],"data":[]},
                            "performanceTrends":{"labels":[],"data":[]},
                            "userActivity":{"labels":[],"data":[]}},
                "tables":  {"topPerformers":[],"recentActivity":[],"examStats":[]},
            })

        # ── Step 3: Collect unique IDs ─────────────────────────────────
        sid_set = {str(r["student_id"]) for r in all_results}
        eid_set = {str(r["exam_id"])    for r in all_results}

        # ── Step 4: Batch fetch usernames via db layer ─────────────────
        user_map = {}
        sid_list = list(sid_set)
        for i in range(0, len(sid_list), 200):
            chunk = [int(x) for x in sid_list[i:i+200] if x]
            if chunk:
                users = get_users_by_ids(chunk)
                user_map.update(users)   # {str(id): {username, full_name}}

        # ── Step 5: Batch fetch exam names ────────────────────────────
        exams     = get_all_exams()
        exam_map  = {str(e["id"]): e.get("name", f"Exam {e['id']}") for e in exams}

        # ── Step 6: Compute analytics ─────────────────────────────────
        total = len(all_results)
        pcts  = [float(r.get("percentage") or 0) for r in all_results]
        avg   = sum(pcts) / total if total else 0
        passed = sum(1 for p in pcts if p >= 40)

        dist = [
            sum(1 for p in pcts if p >= 90),
            sum(1 for p in pcts if 75 <= p < 90),
            sum(1 for p in pcts if 60 <= p < 75),
            sum(1 for p in pcts if p <  60),
        ]

        exam_perf: dict = {}
        for r in all_results:
            name = exam_map.get(str(r["exam_id"]), f"Exam {r['exam_id']}")
            exam_perf.setdefault(name, []).append(float(r.get("percentage") or 0))

        stud: dict = {}
        for r in all_results:
            stud.setdefault(str(r["student_id"]), []).append(float(r.get("percentage") or 0))

        top = []
        for sid, scores in sorted(stud.items(),
                                   key=lambda x: sum(x[1])/len(x[1]) if x[1] else 0,
                                   reverse=True)[:10]:
            u = user_map.get(str(sid), {})
            top.append({
                "student_id": sid,
                "username":   u.get("username") or f"User {sid}",
                "full_name":  u.get("full_name", ""),
                "avgScore":   round(sum(scores)/len(scores), 2),
                "attempts":   len(scores),
            })

        recent = []
        for r in sorted(all_results, key=lambda x: x.get("completed_at",""), reverse=True)[:20]:
            u = user_map.get(str(r.get("student_id", "")), {})
            recent.append({
                "created_at": r.get("completed_at", ""),
                "username":   u.get("username") or f"User {r.get('student_id','')}",
                "full_name":  u.get("full_name", ""),
                "exam_name":  exam_map.get(str(r.get("exam_id", "")), "Unknown"),
                "score":      r.get("score"),
                "max_score":  r.get("max_score"),
                "percentage": round(float(r.get("percentage") or 0), 2),
            })

        exam_stats = [
            {
                "name":     name,
                "attempts": len(scores),
                "avgScore": round(sum(scores)/len(scores), 2) if scores else 0,
                "passRate": round(sum(1 for s in scores if s >= 40)/len(scores)*100, 2) if scores else 0,
            }
            for name, scores in exam_perf.items()
        ]

        return jsonify({
            "summary": {
                "avgScore":      round(avg, 2),
                "totalAttempts": total,
                "passRate":      round(passed/total*100, 2) if total else 0,
                "activeUsers":   len(sid_set),
                "scoreChange":0, "attemptsChange":0,
                "passRateChange":0, "usersChange":0,
            },
            "charts": {
                "scoreDistribution": dist,
                "examPerformance":   {
                    "labels": list(exam_perf.keys()),
                    "data":   [round(sum(v)/len(v), 2) for v in exam_perf.values()],
                },
                "performanceTrends": {"labels":[],"data":[]},
                "userActivity":      {"labels":[],"data":[]},
            },
            "tables": {
                "topPerformers":  top,
                "recentActivity": recent,
                "examStats":      exam_stats,
            },
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Failed", "message": str(e)}), 500


@admin_bp.route("/users-analytics/view-result/<int:result_id>/<int:exam_id>")
@require_admin_role
def users_analytics_view_result(result_id, exam_id):
    result = get_result_by_id(result_id)
    if not result: abort(404)
    user  = get_user_by_id(result.get("student_id")) or {"id":result.get("student_id"),"username":"Unknown","full_name":"Unknown","email":""}
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
    from app.services.drive_service import get_image_url as _get_img_url

    result = get_result_by_id(result_id)
    if not result: abort(404)
    user  = get_user_by_id(result.get("student_id")) or {"id": result.get("student_id"), "username": "Unknown"}
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
        student_name=user.get("full_name", user.get("username","Unknown")),
        username=user.get("username","student"),
    )

    username  = user.get("username","student")
    exam_name = exam.get("name","exam").replace(" ","_")

    return send_file(BytesIO(pdf), as_attachment=True,
                     download_name=f"{exam_name}_{username}_result_{result_id}.pdf",
                     mimetype="application/pdf")