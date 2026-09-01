"""
app/services/dashboard_service.py
Single aggregation point for the GLOBAL "Your Updates" notification popup
(Today's Exams, Results Available, Pending Updates, New Messages, Shared
Notebooks, New Exams). This is USER-scoped, not category/portal-scoped —
it aggregates across every category the user can see, since the popup is a
site-wide attention layer shown at portal entry and reachable via the
notifications bell, independent of whichever category is (or isn't yet)
selected in session. The category-scoped student dashboard
(app/routes/web/dashboard.py) is a separate, unrelated concern and does not
call into this module for its own Live/Upcoming/Completed tabs.
Each section is built independently and degrades to an empty default on
failure — one failing subsystem never breaks the rest.
"""

import logging
from typing import Dict, List, Optional

from app.db.dashboard_events import get_seen_event_keys
from app.utils.datetime_service import now_app_tz, today_app_date

log = logging.getLogger(__name__)

# "New Exams" ages out after this many days even if the student never opens
# it, so an ignored exam doesn't nag forever (see requirement #8).
_NEW_EXAM_WINDOW_DAYS = 14
# Sentinel written by the migration backfill for exams that predate the
# created_at column — anything at/before this can never count as "new".
_BACKFILL_SENTINEL_CUTOFF = "2000-01-02"


def should_auto_show_updates() -> bool:
    """True exactly once per login session. The first page the user actually
    reaches after logging in — the category picker for multi-category users,
    or straight to the dashboard for single-category users who skip the
    picker (see categories.select_category()) — gets to auto-open the global
    popup; every later page load this session returns False. This is purely
    "have I auto-shown the popup yet" bookkeeping, unrelated to the
    item-level read/unread state (dashboard_event_seen / chat_unread), which
    is untouched by this and by closing the popup."""
    from flask import session

    if session.get("_updates_auto_shown"):
        return False
    session["_updates_auto_shown"] = True
    session.modified = True
    return True


def get_greeting() -> str:
    """Server-time fallback for the dashboard greeting — used for the
    initial render only. static/shared/greeting.js immediately overrides
    this client-side using the viewer's actual local clock, since the
    server's configured APP_TIMEZONE can differ from where they actually
    are; this keeps the greeting sensible even before that JS runs (or if
    it's disabled)."""
    hour = now_app_tz().hour
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 17:
        return "Good afternoon"
    if 17 <= hour < 21:
        return "Good evening"
    return "Good night"


def get_today_display() -> str:
    """'Tuesday, 25 August 2026' — for the popup header, in APP_TIMEZONE."""
    return now_app_tz().strftime("%A, %d %B %Y")


def get_dashboard_summary(user_id: int) -> Dict:
    """Self-contained entry point for /api/v01/student/updates — one query
    for every exam system-wide (not per-category — there is no per-student
    category restriction to filter by anyway, see app/db/categories.py) plus
    one for the user's own results, then get_dashboard_context() does the
    rest in Python. No N+1: exactly two queries regardless of how many
    categories exist."""
    from app.db.exams import get_all_exams
    from app.db.results import get_results_by_user

    all_exams = get_all_exams()
    user_results = get_results_by_user(user_id)
    return get_dashboard_context(user_id, all_exams, user_results)


def count_items(dash: Dict) -> int:
    """Badge count for the notifications bell — number of actionable items
    currently in the popup."""
    return (
        len(dash.get("today_exams") or [])
        + len(dash.get("results_available") or [])
        + (1 if dash.get("pending_update") else 0)
        + len(dash.get("new_messages") or [])
        + len(dash.get("connection_requests") or [])
        + len(dash.get("connection_responses") or [])
        + len(dash.get("group_additions") or [])
        + len(dash.get("shared_notebooks") or [])
        + len(dash.get("new_exams") or [])
    )


def _safe(fn, default, label):
    try:
        return fn()
    except Exception as e:
        log.warning("[dashboard_service] %s section failed: %s", label, e)
        return default


def get_dashboard_context(
    user_id: int,
    all_exams: List[Dict],
    user_results: List[Dict],
) -> Dict:
    seen = _safe(lambda: get_seen_event_keys(user_id), {}, "seen_keys")
    cat_names = _safe(_get_category_name_map, {}, "categories")

    today_exams = _safe(lambda: _build_today_exams(user_id, all_exams, cat_names), [], "today_exams")
    results_available = _safe(lambda: _build_results_available(user_results, seen, cat_names), [], "results")
    pending_update = _safe(lambda: _build_pending_update(user_id, seen), None, "requests")
    new_messages = _safe(lambda: _build_new_messages(user_id), [], "chat")
    connection_requests = _safe(lambda: _build_connection_requests(user_id), [], "connection_requests")
    connection_responses = _safe(lambda: _build_connection_responses(user_id, seen), [], "connection_responses")
    group_additions = _safe(lambda: _build_group_additions(user_id, seen), [], "group_additions")
    shared_notebooks = _safe(lambda: _build_shared_notebooks(user_id, seen), [], "notebooks")
    new_exams = _safe(lambda: _build_new_exams(all_exams, seen, cat_names), [], "new_exams")

    summary_parts = []
    if today_exams:
        summary_parts.append(f"{len(today_exams)} exam{'s' if len(today_exams) != 1 else ''} today")
    if results_available:
        summary_parts.append(f"{len(results_available)} result{'s' if len(results_available) != 1 else ''} available")
    if pending_update:
        summary_parts.append("1 update")
    if new_messages:
        summary_parts.append(f"{len(new_messages)} new message{'s' if len(new_messages) != 1 else ''}")
    if connection_requests:
        summary_parts.append(f"{len(connection_requests)} connection request{'s' if len(connection_requests) != 1 else ''}")
    if connection_responses:
        summary_parts.append(f"{len(connection_responses)} connection update{'s' if len(connection_responses) != 1 else ''}")
    if group_additions:
        summary_parts.append(f"added to {len(group_additions)} group{'s' if len(group_additions) != 1 else ''}")
    if shared_notebooks:
        summary_parts.append(f"{len(shared_notebooks)} notebook{'s' if len(shared_notebooks) != 1 else ''} shared")
    if new_exams:
        summary_parts.append(f"{len(new_exams)} new exam{'s' if len(new_exams) != 1 else ''}")

    return {
        "greeting": get_greeting(),
        "summary_parts": summary_parts,
        "has_any": bool(today_exams or results_available or pending_update or new_messages
                         or connection_requests or connection_responses or group_additions
                         or shared_notebooks or new_exams),
        "today_exams": today_exams,
        "results_available": results_available,
        "pending_update": pending_update,
        "new_messages": new_messages,
        "connection_requests": connection_requests,
        "connection_responses": connection_responses,
        "group_additions": group_additions,
        "shared_notebooks": shared_notebooks,
        "new_exams": new_exams,
    }


def _get_category_name_map() -> Dict[str, str]:
    """One query, reused for every exam-related section so each item can be
    labelled with which portal/category it belongs to — required now that
    the popup aggregates across all of them at once."""
    from app.db.categories import get_all_categories

    return {str(c["id"]): c.get("name", "") for c in get_all_categories()}


def _build_today_exams(user_id: int, all_exams: List[Dict], cat_names: Dict[str, str]) -> List[Dict]:
    from app.services.exam_service import (
        compute_exam_action_state, get_exam_time_window, get_effective_status, is_exam_window_open,
    )

    today = today_app_date()
    out = []
    for exam in all_exams:
        if exam.get("date") != today:
            continue
        # Effective status: the literal status column for a Manual Exam
        # (unchanged — status only decides which icon/badge to show and
        # whether to show a countdown at all, never startability by
        # itself); for a Scheduled Exam, the live time-computed state, so
        # it correctly stops appearing "Upcoming" the instant it actually
        # goes live and a cancelled Scheduled Exam is filtered out
        # entirely below — no admin action required either way.
        status = get_effective_status(exam)
        if status in ("draft", "cancelled"):
            continue
        window = get_exam_time_window(exam)
        has_started = bool(window.get("has_started"))
        has_ended = bool(window.get("has_ended"))
        if exam.get("scheduled_mode"):
            # A Scheduled Exam has no separate admin 'ongoing' override —
            # its effective status IS the time-based state. The buffer/
            # "closing" period still reports 'ongoing' here (matching the
            # badge shown elsewhere) but is no longer startable; can_start
            # from compute_exam_action_state() below is what actually
            # gates the Start button — this only affects badge/countdown.
            is_startable = status == "ongoing" and not has_ended
            is_window_ended = False
        else:
            # FIX: this used to independently recompute "started and not
            # ended" inline, which had drifted from is_exam_window_open()'s
            # own rule (a Manual Exam only ever auto-unlocks via an
            # explicit admin status of 'ongoing' — see that function's
            # docstring for the full incident this closes). Calling the
            # same function used everywhere else a Manual Exam's real
            # startability is decided (the /start endpoint, the
            # instructions page) is what keeps this notification
            # consistent with them, instead of showing "Start"/"Live" for
            # an exam the admin explicitly left "Upcoming".
            is_startable = is_exam_window_open(exam)
            is_window_ended = status == "upcoming" and has_ended
        entry = {
            "exam": exam,
            "status": status,
            "is_window_ended": is_window_ended,
            "is_startable": is_startable,
            "category_name": cat_names.get(str(exam.get("category_id")), ""),
            **window,
        }
        if status in ("ongoing", "upcoming"):
            entry.update(compute_exam_action_state(user_id, exam))
        out.append(entry)
    out.sort(key=lambda e: e["exam"].get("start_time") or "")
    return out


def _build_results_available(user_results: List[Dict], seen: Dict, cat_names: Dict[str, str]) -> List[Dict]:
    from app.db.exams import get_exams_by_ids_full
    from app.services.result_service import can_user_see_result

    seen_ids = seen.get("result", set())
    # user_results is already completed_at DESC; a defensive cap bounds the
    # work done per popup load regardless of a student's full history.
    candidates = [r for r in user_results[:30] if str(r.get("id")) not in seen_ids]
    if not candidates:
        return []

    exam_map = get_exams_by_ids_full([c["exam_id"] for c in candidates])
    out = []
    for r in candidates:
        exam = exam_map.get(str(r.get("exam_id")))
        if not exam:
            continue
        visible, _reason = can_user_see_result(exam, r)
        if visible:
            out.append({
                "result": r,
                "exam": exam,
                "category_name": cat_names.get(str(exam.get("category_id")), ""),
            })
        if len(out) >= 5:
            break
    return out


def _build_pending_update(user_id: int, seen: Dict) -> Optional[Dict]:
    from app.db.users import get_user_profile_by_id
    from app.db.misc import get_requests_by_user

    user = get_user_profile_by_id(user_id)
    if not user:
        return None
    reqs = get_requests_by_user(user.get("username", ""), user.get("email", ""))
    if not reqs:
        return None

    latest = reqs[0]
    status = latest.get("request_status")
    if status == "pending":
        return latest
    if str(latest.get("request_id")) not in seen.get("request_status", set()):
        return latest
    return None


def _build_new_messages(user_id: int, limit: int = 3) -> List[Dict]:
    from app.services.chat_service import get_conversations_for_user

    convs = get_conversations_for_user(user_id)
    unread = [c for c in convs if c.get("unread", 0) > 0]
    return unread[:limit]  # already sorted by last-message time, newest first


def _build_connection_requests(user_id: int, limit: int = 5) -> List[Dict]:
    """Pending connection/friend requests addressed to me. No seen-gating —
    like the exam-access "pending" case, this stays visible for as long as
    it's genuinely unresolved (actionable), not a one-time event; it
    naturally disappears once accepted/declined since the underlying query
    only matches status='pending'."""
    from app.db.chat import get_pending_requests_for
    from app.db.users import get_users_by_ids
    from app.services.image_storage_service import profile_photo_url_from_key

    rows = get_pending_requests_for(user_id)
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True)[:limit]

    users = get_users_by_ids([r["requester_id"] for r in rows])
    out = []
    for r in rows:
        u = users.get(str(r["requester_id"]), {})
        out.append({
            "connection_id": r["id"],
            "requester_name": u.get("full_name") or u.get("username") or "Someone",
            "photo_url": profile_photo_url_from_key(u.get("profile_photo_key")),
        })
    return out


def _build_connection_responses(user_id: int, seen: Dict, limit: int = 5) -> List[Dict]:
    """Requests *I* sent that were since accepted/rejected — one-time
    events, seen-gated. There's no natural "view" page for a rejected
    request (no conversation gets created), so this is dismissed explicitly
    from the popup rather than by opening something."""
    from app.db.chat import get_recent_resolved_requests_for_requester
    from app.db.users import get_users_by_ids
    from app.services.image_storage_service import profile_photo_url_from_key

    seen_ids = seen.get("connection_response", set())
    candidates = [
        r for r in get_recent_resolved_requests_for_requester(user_id, limit=15)
        if str(r["id"]) not in seen_ids
    ][:limit]
    if not candidates:
        return []

    users = get_users_by_ids([r["recipient_id"] for r in candidates])
    out = []
    for r in candidates:
        u = users.get(str(r["recipient_id"]), {})
        out.append({
            "connection_id": r["id"],
            "other_name": u.get("full_name") or u.get("username") or "Someone",
            "photo_url": profile_photo_url_from_key(u.get("profile_photo_key")),
            "status": r["status"],
        })
    return out


def _build_group_additions(user_id: int, seen: Dict, limit: int = 5) -> List[Dict]:
    """Groups I was recently added to. Seen-gated by conversation id —
    naturally marked seen when I open that group's chat (see
    app/routes/api/v01/chat.py's message-fetch endpoint, the same place
    chat's own unread counter already resets on open)."""
    from app.db.chat import get_recent_group_memberships
    from app.db.users import get_users_by_ids
    from app.services.image_storage_service import group_photo_url_from_key

    seen_ids = seen.get("group_added", set())
    candidates = [
        g for g in get_recent_group_memberships(user_id, limit=15)
        # A group I created myself isn't something "another user/admin added
        # me to" — I'm only in chat_members for it because creation adds the
        # creator as a member too (see app/routes/api/v01/chat.py:create_group).
        if g.get("created_by") != user_id
        and str(g["conversation_id"]) not in seen_ids
    ][:limit]
    if not candidates:
        return []

    users = get_users_by_ids([g["created_by"] for g in candidates if g.get("created_by")])
    out = []
    for g in candidates:
        u = users.get(str(g.get("created_by")), {})
        out.append({
            "conversation_id": g["conversation_id"],
            "group_name": g.get("group_name") or "Group",
            "added_by_name": u.get("full_name") or u.get("username") or "Someone",
            "photo_url": group_photo_url_from_key(g.get("group_photo_key")),
        })
    return out


def _build_shared_notebooks(user_id: int, seen: Dict, limit: int = 5) -> List[Dict]:
    from app.db.notes import list_recent_shares_for_user

    seen_ids = seen.get("notebook_share", set())
    candidates = list_recent_shares_for_user(user_id, limit=15)
    return [c for c in candidates if str(c.get("share_id")) not in seen_ids][:limit]


def _build_new_exams(all_exams: List[Dict], seen: Dict, cat_names: Dict[str, str], limit: int = 5) -> List[Dict]:
    from datetime import timedelta
    from app.utils.datetime_service import now_utc_naive

    seen_ids = seen.get("new_exam", set())
    # exams.created_at is populated by Postgres's own DEFAULT now() (like every
    # other DEFAULT now() timestamp column in this schema), i.e. UTC — not
    # app-timezone — so the cutoff must be computed against UTC "now" too.
    cutoff = (now_utc_naive() - timedelta(days=_NEW_EXAM_WINDOW_DAYS)).isoformat()

    candidates = [
        e for e in all_exams
        # A cancelled Scheduled Exam (status column holds the literal
        # 'cancelled' override — see get_effective_status()) must never be
        # advertised as a "new exam" either, same as it must never become
        # accessible or startable.
        if str(e.get("status", "draft")).lower().strip() not in ("draft", "cancelled")
        and str(e.get("created_at") or "") > _BACKFILL_SENTINEL_CUTOFF
        and str(e.get("created_at") or "") > cutoff
        and str(e.get("id")) not in seen_ids
    ]
    candidates.sort(key=lambda e: e.get("created_at") or "", reverse=True)

    from app.services.exam_service import get_exam_time_window

    return [
        {
            **e,
            "category_name": cat_names.get(str(e.get("category_id")), ""),
            "start_time_ampm": get_exam_time_window(e).get("start_time_ampm") or e.get("start_time"),
        }
        for e in candidates[:limit]
    ]
