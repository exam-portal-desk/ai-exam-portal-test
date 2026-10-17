"""
app/routes/api/v01/admin/attempts.py
Admin attempt-management JSON API (v01).

  GET  /api/v01/admin/attempts/search       one row per (student, exam): used / running / limit / status
  POST /api/v01/admin/attempts/modify       reset, increase or decrease one student's attempts on one exam
  POST /api/v01/admin/attempts/bulk-modify  the same for several (student, exam) pairs

What "used" means: an attempt counts against the exam's limit once it has completed, or when an administrator added it by
hand (status 'manual_add'). This is the same count the student-side start and resume checks use
(app.db.attempts.get_used_attempts_count), so this page and the exam itself always agree. An attempt that is still
running ('in_progress' / 'finalizing') is shown separately and is never deleted by these actions.
"""
from flask import request, jsonify
from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_permission
from app.db import fetch_all, execute, insert_many
from app.db.attempts import ATTEMPT_STATUS_FILTERS, search_attempts
from app.utils.datetime_service import now_utc_naive

PER_PAGE = 50
MAX_AMOUNT = 1000


def _bad_request(message):
    return jsonify({"success": False, "message": message}), 400


def _row(r):
    limit = r.get("max_attempts")
    used = int(r["used"])
    if not limit or int(limit) <= 0:
        shown_max, remaining, status = "∞", "∞", "unlimited"
    else:
        limit = int(limit)
        shown_max, remaining = str(limit), max(limit - used, 0)
        status = "exhausted" if used >= limit else "available"
    return {
        "student_id":    r["student_id"],
        "username":      r.get("username") or "Deleted User",
        "exam_id":       r["exam_id"],
        "exam_name":     r.get("exam_name") or f"Exam {r['exam_id']}",
        "max_attempts":  shown_max,
        "attempts_used": used,
        "in_progress":   int(r.get("running") or 0),
        "remaining":     remaining,
        "status":        status,
    }


@admin_api_bp.route("/attempts/search")
@require_admin_permission("attempt_management")
def api_attempts_search():
    """
    Query params (all optional, combined with AND):
      q        username or full name contains this text (case-insensitive)
      exam_id  one exam
      status   unlimited | available | exhausted | in_progress
      page     1-based; a page past the end returns the last page that exists
    The filters, the total and the page window are all computed in one SQL statement.
    """
    q = request.args.get("q", "").strip()
    exam_raw = request.args.get("exam_id", "").strip()
    status = request.args.get("status", "").strip()
    if status and status not in ATTEMPT_STATUS_FILTERS:
        return _bad_request("Unknown status filter.")
    exam_id = None
    if exam_raw:
        try:
            exam_id = int(exam_raw)
        except ValueError:
            return _bad_request("exam_id must be a number.")
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    try:
        rows, total, page = search_attempts(q, exam_id, status, page, PER_PAGE)
    except Exception as e:
        print(f"[admin.attempts] search error: {e}")
        return jsonify({"success": False, "message": "Could not load attempts. Please try again."}), 500

    return jsonify({
        "rows":        [_row(r) for r in rows],
        "total":       total,
        "page":        page,
        "per_page":    PER_PAGE,
        "total_pages": max(1, -(-total // PER_PAGE)),
    })


def _apply_attempt_action(student_id: int, exam_id: int, action: str, amount: int):
    """Shared by modify and bulk-modify. Only counted attempts (completed / manual_add) are ever deleted; a running attempt is
    never touched."""
    if action not in ("reset", "increase", "decrease"):
        return False, "Invalid action"

    rows = fetch_all(
        "SELECT id, status, attempt_number FROM exam_attempts WHERE student_id=%s AND exam_id=%s",
        (student_id, exam_id),
    )
    counted = [r for r in rows if r["status"] in ("completed", "manual_add")]

    if action == "reset":
        if counted:
            execute("DELETE FROM exam_attempts WHERE id = ANY(%s)", ([r["id"] for r in counted],))

    elif action == "decrease":
        if len(counted) < amount:
            return False, f"Only {len(counted)} used attempt(s) can be removed."
        # hand-added attempts go first, then the most recent completed ones
        order = sorted(counted, key=lambda r: (r["status"] != "manual_add", -int(r["id"])))
        execute("DELETE FROM exam_attempts WHERE id = ANY(%s)", ([r["id"] for r in order[:amount]],))

    else:
        top = max((int(r.get("attempt_number") or 0) for r in rows), default=0)
        now = now_utc_naive().isoformat()
        insert_many("exam_attempts", [
            {"student_id": student_id, "exam_id": exam_id, "attempt_number": top + i + 1,
             "status": "manual_add", "start_time": now, "end_time": None}
            for i in range(amount)
        ])
    return True, None


def _parse_amount(raw):
    try:
        amount = int(raw if raw not in (None, "") else 1)
    except (TypeError, ValueError):
        return None
    return amount if 1 <= amount <= MAX_AMOUNT else None


def _parse_pair(student_id, exam_id):
    try:
        return int(student_id), int(exam_id)
    except (TypeError, ValueError):
        return None


@admin_api_bp.route("/attempts/modify", methods=["POST"])
@require_admin_permission("attempt_management")
def attempts_modify():
    p = request.get_json(silent=True) or {}
    pair = _parse_pair(p.get("student_id"), p.get("exam_id"))
    amount = _parse_amount(p.get("amount"))
    if pair is None:
        return _bad_request("A student and an exam are required.")
    if amount is None:
        return _bad_request(f"Amount must be a whole number from 1 to {MAX_AMOUNT}.")
    try:
        ok, err = _apply_attempt_action(pair[0], pair[1], p.get("action", ""), amount)
    except Exception as e:
        print(f"[admin.attempts] modify error: {e}")
        return jsonify({"success": False, "message": "Could not change the attempts. Nothing was saved."}), 500
    if not ok:
        return _bad_request(err)
    return jsonify({"success": True})


@admin_api_bp.route("/attempts/bulk-modify", methods=["POST"])
@require_admin_permission("attempt_management")
def attempts_bulk_modify():
    data = request.get_json(silent=True) or {}
    items = data.get("items")
    amount = _parse_amount(data.get("amount"))
    if not isinstance(items, list) or not items:
        return _bad_request("Select at least one student and exam.")
    if amount is None:
        return _bad_request(f"Amount must be a whole number from 1 to {MAX_AMOUNT}.")

    done, errors = 0, []
    for item in items:
        pair = _parse_pair(item.get("student_id"), item.get("exam_id")) if isinstance(item, dict) else None
        if pair is None:
            errors.append("Skipped an entry without a valid student and exam.")
            continue
        try:
            ok, err = _apply_attempt_action(pair[0], pair[1], data.get("action", ""), amount)
        except Exception as e:
            print(f"[admin.attempts] bulk item error: {e}")
            errors.append(f"student {pair[0]} / exam {pair[1]}: could not be changed")
            continue
        if ok:
            done += 1
        else:
            errors.append(f"student {pair[0]} / exam {pair[1]}: {err}")

    return jsonify({"success": done > 0, "processed": done, "errors": errors or None,
                    "message": None if done else (errors[0] if errors else "Nothing was changed.")})
