"""
app/routes/api/v01/admin/analytics.py
Admin results-analytics JSON API (v01). Relocated from
app/routes/admin/results.py.

  GET /admin/api/users-analytics/stats -> GET /api/v01/admin/analytics/stats
  GET /admin/api/users-analytics/data  -> GET /api/v01/admin/analytics/data

NOTE on scope: users_analytics_data_api's aggregation (fetch up to 50K
result rows in 1000-row batches, then compute score distribution/top
performers/exam stats in Python) is flagged in the architecture audit as
worth pushing into SQL, but is explicitly self-capped and was not ranked
as urgent — given the complexity of that aggregation (per-student
top-10, per-exam grouping, score-bucket histogram) and no test
environment to verify a SQL rewrite against, it's relocated as-is rather
than rewritten here.
"""
from flask import request, jsonify
from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_role
from app.db import fetch_one, fetch_all

DELETED_USER_LABEL = "Deleted User"


def _display_username(user: dict) -> str:
    return user.get("username") or DELETED_USER_LABEL


def _display_full_name(user: dict) -> str:
    return user.get("full_name") or user.get("username") or DELETED_USER_LABEL


@admin_api_bp.route("/analytics/stats")
@require_admin_role
def api_users_analytics_stats():
    """
    One round trip (scalar subqueries) instead of 4 sequential COUNT
    queries (flagged in the architecture audit) — instant response even
    with millions of rows.
    """
    try:
        row = fetch_one(
            "SELECT (SELECT COUNT(*) FROM users) AS total_users, "
            "(SELECT COUNT(*) FROM exams) AS total_exams, "
            "(SELECT COUNT(*) FROM results) AS total_results, "
            "(SELECT COUNT(*) FROM responses) AS total_responses"
        )
        return jsonify({
            "total_users":     row["total_users"],
            "total_exams":     row["total_exams"],
            "total_results":   row["total_results"],
            "total_responses": row["total_responses"],
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({
            "total_users":0, "total_exams":0,
            "total_results":0, "total_responses":0
        }), 500


@admin_api_bp.route("/analytics/data")
@require_admin_role
def users_analytics_data_api():
    from datetime import datetime as dt, timedelta
    from app.db.exams import get_all_exams
    from app.db.users import get_users_by_ids
    from app.utils.datetime_service import format_display

    time_period = (request.args.get("timePeriod") or "all").lower()
    exam_filter = (request.args.get("exam") or "").strip()
    start_date  = request.args.get("startDate", "")
    end_date    = request.args.get("endDate",   "")
    now         = dt.now()

    try:
        # ── Step 1: Smart date range → fetch only needed slice ────────
        # Instead of fetching all 200K rows, use DB-level date filter
        # and fetch in ONE paginated batch (max ~50K for analytics is fine)

        where, params = [], []
        if exam_filter:
            where.append("exam_id=%s"); params.append(exam_filter)

        if time_period == "today":
            where.append("completed_at >= %s"); params.append(now.strftime("%Y-%m-%d"))
        elif time_period == "week":
            week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
            where.append("completed_at >= %s"); params.append(week_start)
        elif time_period == "month":
            where.append("completed_at >= %s"); params.append(now.strftime("%Y-%m-01"))
        elif time_period == "custom" and start_date and end_date:
            where.append("completed_at >= %s AND completed_at <= %s")
            params += [start_date, end_date + "T23:59:59"]

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        base_query = (
            f"SELECT id, student_id, exam_id, score, max_score, percentage, completed_at "
            f"FROM results {where_sql} ORDER BY completed_at DESC"
        )

        # ── Step 2: Paginated fetch with 1000-row chunks ──────────────
        PAGE = 1000
        offset = 0
        all_results = []

        while True:
            batch = fetch_all(f"{base_query} LIMIT %s OFFSET %s", params + [PAGE, offset])
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
                "username":   _display_username(u),
                "full_name":  _display_full_name(u),
                "avgScore":   round(sum(scores)/len(scores), 2),
                "attempts":   len(scores),
            })

        recent = []
        for r in sorted(all_results, key=lambda x: x.get("completed_at",""), reverse=True)[:20]:
            u = user_map.get(str(r.get("student_id", "")), {})
            recent.append({
                "created_at": format_display(r.get("completed_at")),
                "username":   _display_username(u),
                "full_name":  _display_full_name(u),
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
