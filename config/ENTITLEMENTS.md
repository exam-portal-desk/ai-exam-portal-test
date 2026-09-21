# Plans, feature access and limits

`config/entitlements.json` defines what each plan contains. `app/entitlements/` answers "may this user use this feature, and how much?". The database stores only exceptions (a user's plan, per-user grants and denies, an audit trail). Admin roles stay in `users.role` and are never part of a plan.

The Admin-facing summary is the **Plans & Access** section of the Admin Guide; it is generated from this file, so it cannot drift.

## What is configured today

| Plan | Contains |
|---|---|
| **Free** (default) | Exams, Results, Responses, Result history, Analytics |
| **Pro** | Free + AI Assistant (50 requests per day, 100 messages per conversation), Student chat, Question discussion |
| **Elite** | Pro + Discuss with AI, AI Explanation (50 per day, 3 per question), Notebook |

Limits exist only where a number is written. Everything else is unlimited and not metered: Notebook, Analytics and the rest have no limit at all.

**This file is the only place plans, features and limits are defined.** `.env` holds secrets and infrastructure only. The four per-student limits that used to be environment variables (`AI_DAILY_LIMIT_PER_STUDENT`, `MAX_MESSAGES_PER_CONVERSATION`, `EXPLANATION_DAILY_LIMIT`, `EXPLANATION_PER_QUESTION_LIMIT`) were moved here with the values that were live, and no longer exist anywhere else.

Pro and Elite inherit from the plan above them (`"inherits"`). A plan does not have to inherit anything; an independent plan just lists its own features.

### How a user gets a plan

- **Where it is stored:** `users.plan` and `users.plan_expires_at`. `NULL` means no plan was chosen, and the account uses `default_plan` from this file: **Free**. So every account that existed before plans did, and every new account, is Free by itself. **Nobody has to be set up to be on Free.** A plan whose expiry has passed falls back to Free the same way, and so does a plan name that no longer exists in this file.
- **Who assigns it:** an administrator (Users & Requests, Users tab: click the plan badge, choose the plan, Save; the API can also set an expiry), or later a billing system through `entitlements.set_user_plan(user_id, plan, expires_at, actor_id)`. A student cannot change their own plan.
- **Where it is shown:** the user portal sidebar and the profile page ("Your Plan": the plan and exactly what it includes), the locked page, `GET /api/v01/me/plan`, and the Users tab. All of them read `entitlements.user_plan()`, the same function the server checks use, so what is shown is what is enforced.
- **To give everyone more for a while:** change `default_plan` (for example to `"elite"`); no user data changes.

### What is enforced

Every student feature is `"enforced": true`, and the **server** checks the plan on every request; hiding a link is only a courtesy on top. A student whose plan does not include a feature gets a 403: the locked page in a browser, JSON (`feature_locked`, `plan_label`, `required_plan`, ...) from an API. Where:

| Feature | Checked in |
|---|---|
| `ai_assistant` | the whole AI Assistant API and page (`app/routes/api/v01/assistant.py`, `app/routes/web/ai_assistant.py`) |
| `response_ai_discussion` | the Assistant's question focus (the `focus` routes and a focused message) |
| `ai_explanation` | the explanation API (`app/routes/api/v01/explanations.py`) |
| `chat` | the chat page, API and socket events |
| `discussion` | the discussion API and its socket room |
| `notebook` | all notebook pages and the notebook API |
| `exams`, `results`, `responses`, `result_history`, `analytics` | their entry pages/APIs (`exam-instructions`, exam start, result, response, history, analytics) |

`app/entitlements/guard.py` provides `require_feature("...")` for one route and `gate_blueprint(bp, "...")` for a blueprint. Admin-portal sessions and visitors are not gated by it (the login and admin guards handle them); admin permissions use `require_admin_permission`.

A grant (with optional `limits`) opens a feature for one user, and a deny closes it, on top of the plan. Limits always come from this file.

## The file

```json
{
  "default_plan": "free",
  "features": {
    "ai_assistant": {
      "category": "student", "label": "AI Assistant", "description": "...", "enforced": true,
      "limits": { "requests_per_day": { "label": "Requests per day", "per": "day" } }
    },
    "admin.ai_configuration": { "category": "admin", "label": "AI configuration", "admin_scope": "all", "enforced": true }
  },
  "plans": {
    "pro": { "label": "Pro", "tone": "info", "inherits": "free",
             "features": { "ai_assistant": { "limits": { "requests_per_day": 50 } }, "chat": {} } }
  }
}
```

- **Feature keys** are stable machine names. Admin permissions are the only keys starting `admin.`; plans cannot contain them.
- A feature declares which limits **may** exist (`limits`, with a label). A plan then sets numbers for those keys only. A limit key that is left out is unlimited. Do not use `-1` or a huge number for "unlimited"; the file is rejected if a limit is not a whole number of 0 or more.
- A plan entry `{}` means "included, no limits". `{"enabled": false}` removes a feature the plan would inherit. A plan's entry for a feature **replaces** the inherited one (including its limits).
- `tone` and `icon` only decide how the plan badge looks (Free: `dark`/`fa-leaf`, Pro: `royal`/`fa-bolt`, Elite: `crimson`/`fa-crown`); they never affect access.
- `admin_scope`: `"all"` = every admin has it; `"granted"` = only admins with an explicit grant.
- `step_up: "passkey"` marks a feature as needing extra authentication (see below).

The file is re-read when it changes (checked every 30 seconds). A file with a mistake is refused with a clear message and the last good version keeps serving.

## How a decision is made

For a feature the catalog enforces, first match wins:

1. An active **deny** override for that user and feature: denied. Nothing overrides a deny, including the admin role.
2. Admin feature (`admin.*`): the user must have the admin role. Then an active grant allows it; otherwise it is allowed if `admin_scope` is `all`, else denied.
3. Student feature: allowed if it is in the user's effective plan or the user has an active grant (a grant's `limits` replace the plan's for those keys).
4. Otherwise denied.

Overrides and plans stop applying at their `expires_at`; an expired or missing plan means `default_plan`. The admin role never grants a student feature, and a plan never grants an admin feature. A feature with `"enforced": false` is allowed for everyone (a staging switch for a feature whose code is not gated yet); every student feature is enforced today.

## Asking from code

```python
from app import entitlements

entitlements.has_access(user_id, "notebook")
entitlements.limit_for(user_id, "ai_explanation", "per_day")                    # int, or None = unlimited
entitlements.check_limit(user_id, "notebook", "notebooks", used=current_count)  # .allowed .remaining .limit
```

Code that shows or enforces a limit must treat `None` as "no limit" (the AI Assistant, the AI Explanation and their pages already do). Asking for a limit the feature does not declare raises `AccessError`, so a typo can never turn into "unlimited". `used` is a number the caller already has (a counter it keeps, or a count it just read), so a check never adds a query of its own. The database is read only for an enforced feature, or a limit check on a feature that declares limits: once per user per minute (cached, and refreshed immediately by any change made here). `has_access` on a feature that is not enforced reads nothing.

Notebook limits are enforced in `app/services/notes_service.py` (create, import, restore from Trash, add page) with the count and the insert in one transaction per owner or notebook (`app/db/notes.py`), so several tabs or direct API calls cannot go past them; a refusal is a 429 with `limit_reached`, `limit_key`, `limit` and a ready-to-show `message`. Existing notebooks and pages stay editable. Live call sites today: the AI Assistant (`requests_per_day`, `messages_per_conversation`) in `app/routes/api/v01/assistant.py` via `app/services/ai_service.py`, and the AI Explanation (`per_day`, `per_question`) in `app/services/explanation_service.py`. `require_admin_permission("access_control")` in `app/middleware/session_guard.py` is the route decorator for admin permissions; a permission not listed in the catalog is not gated.

If `migrations/20260923_entitlements.sql` has not been applied, everything keeps working with the default plan and no overrides.

## Recipes

| Goal | Edit |
|---|---|
| Give Notebook to Pro | Move `"notebook": {}` from `elite` to `pro`. |
| Notebook limits for a plan | In that plan's `features`: `"notebook": {"limits": {"notebooks": 5, "pages_per_notebook": 20}}` (a plan that lists `notebook` includes the feature). `notebooks` = how many a user owns at once (Trash does not count), `pages_per_notebook` = pages in one notebook. Leave a limit out for unlimited; `"notebook": {}` is unlimited on both. |
| Make analytics unlimited | Leave `limits` out of its entry. |
| New plan | Add it under `plans` (optionally `inherits`). It appears in the Users tab automatically. |
| New feature | Add it under `features`, then list it in the plans that include it. |
| Gate a new feature | Add it to `features` (with `"enforced": true`) and to the plans that include it, then protect its routes with `require_feature("...")` / `gate_blueprint(...)`; `tests/test_entitlements.py` lists the gated modules. |
| Change how a plan looks | Its `tone` (`dark`, `royal`, `crimson`, `neutral`, `info`, `accent`, `success`, `warning`) and `icon` (a Font Awesome name such as `fa-crown`) in `plans`; the badge style is `static/shared/plan-badge.css`. |
| Restrict an admin permission to chosen admins | Set that feature's `admin_scope` to `"granted"` and grant it per admin. |
| Temporary access for one user | Users & Requests, Users tab: click the plan or the Custom Access chip, then **Add access** (Grant or Deny, feature, days, reason); the × removes it. Or `PUT /api/v01/admin/access/users/<id>/overrides/<feature>` with `{"effect": "grant", "days": 30, "limits": {...}, "reason": "..."}`; `DELETE` the same URL to remove. |
| Change a user's plan | Users & Requests, Users tab: click the plan chip. Or `PUT .../users/<id>/plan` with `{"plan": "pro"}`; `days` / `expires_at` make it temporary. |

## Storage

`migrations/20260923_entitlements.sql`:

- `users.plan`, `users.plan_expires_at`: `NULL` is the default plan, so existing accounts need no data change.
- `user_feature_overrides`: one row per user and feature, only for exceptions (`effect` grant or deny, optional `limits`, `expires_at`, `reason`, `created_by`).
- `access_audit_log`: who changed which plan or override, and when.

There is deliberately no usage-counter table: the AI limits are measured by the existing counters (`ai_usage_tracking`, `ai_explanation_usage`; the per-conversation cap by the conversation's own message count), and plan limits are only compared with those numbers. Add a counter table when a new limit needs one that does not exist.

## Not built yet

- Access requests for a plan or a feature: the student-side request and the approval screen. Role requests (`requests_raised`) are unchanged; approving a future feature request should call `entitlements.set_override` (or `set_user_plan`).
- Limits for Notebook, chat and the other features: none exist today, so none are declared. When one is added, declare it on the feature, set it in the plans, and read it with `limit_for` / `check_limit` where the resource is created.
- Passkey/WebAuthn: `step_up` is parsed and returned on every access decision (`Access.step_up`); nothing verifies it yet, and no feature sets it.
- Billing: a payment system only needs to call `entitlements.set_user_plan(user_id, plan, expires_at, actor_id)`.
