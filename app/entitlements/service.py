"""
app/entitlements/service.py
The one API the rest of the application uses to ask "may this user use this feature, and how much of it?".
Callers never see plans, overrides or tables.

A feature the catalog does not enforce yet is answered from the catalog alone, with no database read.
"""

from dataclasses import replace
from datetime import datetime
from typing import Dict, Iterable, Optional

from app.entitlements import authority, store
from app.entitlements.catalog import ADMIN, STUDENT, Catalog, get_catalog, parse_limits
from app.entitlements.errors import AccessDenied, AccessError
from app.entitlements.resolver import DENY, GRANT, Access, LimitStatus, Subject, check_limit as _check_limit, effective_plan, resolve
from app.utils.datetime_service import format_display_date, now_utc_naive


def _access(user_id: int, feature: str, is_admin: Optional[bool] = None, need_limits: bool = False) -> Access:
    """A feature that is not enforced is answered from the catalog alone; the user is only read when their own plan or a
    grant can change its limits."""
    catalog = get_catalog()
    definition = catalog.features.get(feature)
    load = definition is not None and (definition.enforced or (need_limits and bool(definition.limits)))
    subject = store.load_subject(user_id, is_admin) if load else None
    return resolve(catalog, subject, feature, now_utc_naive())


def check(user_id: int, feature: str, *, is_admin: Optional[bool] = None) -> Access:
    return _access(user_id, feature, is_admin)


def has_access(user_id: int, feature: str, *, is_admin: Optional[bool] = None) -> bool:
    return _access(user_id, feature, is_admin).allowed


def _limited_access(user_id: int, feature: str, limit_key: str) -> Access:
    definition = get_catalog().features.get(feature)
    if definition is None or limit_key not in definition.limits:
        raise AccessError(f"'{feature}' does not declare a limit '{limit_key}'.")
    return _access(user_id, feature, need_limits=True)


def limit_for(user_id: int, feature: str, limit_key: str) -> Optional[int]:
    """The user's limit for a limit the feature declares, or None for unlimited (nothing is configured). A user an enforced
    feature is closed to has a limit of 0."""
    access = _limited_access(user_id, feature, limit_key)
    return access.limits.get(limit_key) if access.allowed else 0


def check_limit(user_id: int, feature: str, limit_key: str, used: int, amount: int = 1) -> LimitStatus:
    return _check_limit(_limited_access(user_id, feature, limit_key), limit_key, used, amount)


def has_admin_permission(user_id: Optional[int], permission: str) -> bool:
    """Whether this person may use the admin feature `admin.<permission>`. The Admin role alone is not enough: the person
    needs an active grant for it. Read from the database role (not the session's), and a name the catalog does not know is
    refused, so a typo can never open a route."""
    if user_id is None or f"admin.{permission}" not in get_catalog().features:
        return False
    return check(user_id, f"admin.{permission}").allowed


def admin_denied_reason(permission: str) -> dict:
    """What the "not allowed" page says. no_root: nobody holds root yet, so the page can point at how to set one up."""
    definition = get_catalog().features.get(f"admin.{permission}")
    try:
        no_root = store.count_active_grants(authority.ROOT) == 0
    except Exception:
        no_root = False
    return {"permission": permission, "label": definition.label if definition else permission, "no_root": no_root}


def authorize_role_change(actor_id: Optional[int], target_id: int) -> None:
    """Raises AccessDenied unless the actor may grant or remove the Admin role of the target."""
    authority.authorize(actor_id, target_id, "role")


def _plan_view(catalog: Catalog, plan_key: Optional[str], expires_at: Optional[datetime], now: datetime) -> dict:
    plan = effective_plan(catalog, Subject(0, plan=plan_key, plan_expires_at=expires_at), now)
    return {"plan": plan.key, "plan_label": plan.label, "tone": plan.tone, "icon": plan.icon}


def summaries_for(user_ids: Iterable[int]) -> Dict[int, dict]:
    """The plan chip and the "Custom Access" flag for a page of users, in two queries. When the access tables are
    unavailable every user simply shows the default plan."""
    catalog, now = get_catalog(), now_utc_naive()
    ids = [int(i) for i in user_ids]
    stored = store.summaries(ids)
    out = {}
    for uid in ids:
        row = stored.get(uid) or {}
        out[uid] = {**_plan_view(catalog, row.get("plan"), row.get("plan_expires_at"), now), "custom": row.get("custom", 0),
                    "admin_access": row.get("admin_access", 0)}
    return out


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def describe_user(user_id: int, actor_id: Optional[int] = None) -> dict:
    """Everything the Access Control views need about one user: plan, admin access, special access and the effective result
    per feature; with `actor_id`, also what that actor may change (the server re-checks every write regardless)."""
    catalog, now = get_catalog(), now_utc_naive()
    subject = store.load_subject(user_id)
    overrides = store.list_overrides(user_id)
    live = replace(subject, overrides=tuple(overrides))
    plan = _plan_view(catalog, subject.plan, subject.plan_expires_at, now)
    rows = []
    for feature in catalog.features.values():
        entitled = resolve(catalog, live, feature.key, now, force=True)
        rows.append({"feature": feature.key, "label": feature.label, "category": feature.category, "enforced": feature.enforced,
                     "allowed": entitled.allowed, "source": entitled.source, "limits": dict(entitled.limits)})
    def entry(o):
        definition = catalog.features.get(o.feature)
        limits = dict(o.limits)
        return {"feature": o.feature, "label": definition.label if definition else o.feature, "effect": o.effect, "limits": limits,
                "limit_text": [f"{definition.limits[k].label}: {v}" for k, v in limits.items() if definition and k in definition.limits],
                "expires_at": _iso(o.expires_at), "reason": o.reason, "active": o.active(now),
                "privileged": bool(definition and definition.privileged)}

    def category(o):
        definition = catalog.features.get(o.feature)
        return definition.category if definition else STUDENT

    return {
        "user_id": int(user_id), "is_admin": subject.is_admin, **plan,
        "plan_key": subject.plan, "plan_expires_at": _iso(subject.plan_expires_at),
        "special": [entry(o) for o in overrides if category(o) == STUDENT],
        "admin_access": [entry(o) for o in overrides if category(o) == ADMIN],
        "features": rows,
        "capabilities": authority.capabilities(actor_id, user_id) if actor_id is not None else None,
    }


def _expiry_view(catalog: Catalog, subject: Subject, now: datetime) -> dict:
    """The plan expiry a person is shown, from the one stored value the resolver also uses (users.plan_expires_at), formatted
    by the central date service (APP_TIMEZONE + DISPLAY_DATE_FORMAT). state: "none" (no expiry, which includes every account
    that never had a plan set), "active" (expires on expires_display) or "expired" (that plan has lapsed and the account is
    already on the default plan, expired_plan_label says which plan)."""
    if not subject.plan or subject.plan_expires_at is None or subject.plan not in catalog.plans:
        return {"state": "none", "expires_at": None, "expires_display": None, "expired_plan_label": None}
    lapsed = subject.plan_expires_at <= now
    stored = catalog.plans.get(subject.plan)
    return {"state": "expired" if lapsed else "active", "expires_at": _iso(subject.plan_expires_at),
            "expires_display": format_display_date(subject.plan_expires_at),
            "expired_plan_label": stored.label if lapsed else None}


def user_plan(user_id: int) -> dict:
    """What the user portal shows and what the checks enforce, from the same catalog and the same stored plan: the effective
    plan, and for every student feature whether this user may use it and, if not, which plan includes it."""
    catalog, now = get_catalog(), now_utc_naive()
    subject = store.load_subject(user_id)
    features = []
    for feature in catalog.student_features():
        access = resolve(catalog, subject, feature.key, now)
        entry = catalog.entry_plan(feature.key)
        need = entry if entry and not access.allowed else None
        features.append({"key": feature.key, "label": feature.label, "description": feature.description, "allowed": access.allowed,
                         "required_plan": need.label if need else None, "required_tone": need.tone if need else None,
                         "required_icon": need.icon if need else None,
                         "limits": [f"{feature.limits[k].label}: {v}" for k, v in access.limits.items() if k in feature.limits]})
    return {**_plan_view(catalog, subject.plan, subject.plan_expires_at, now), "expires_at": _iso(subject.plan_expires_at),
            "expiry": _expiry_view(catalog, subject, now),
            "custom": sum(1 for o in subject.overrides if o.active(now) and o.feature in catalog.features
                          and catalog.features[o.feature].category == STUDENT), "features": features}


def locked_reason(user_id: int, feature: str) -> dict:
    """What to tell a user who may not use `feature`: the feature, their plan and the plan that includes it."""
    catalog = get_catalog()
    definition = catalog.features.get(feature)
    entry = catalog.entry_plan(feature)
    mine = user_plan(user_id)
    return {"feature": feature, "feature_label": definition.label if definition else feature,
            "plan": mine["plan"], "plan_label": mine["plan_label"], "plan_tone": mine["tone"], "plan_icon": mine["icon"],
            "required_plan": entry.label if entry else None, "required_tone": entry.tone if entry else None,
            "required_icon": entry.icon if entry else None}


def set_user_plan(user_id: int, plan: Optional[str], expires_at: Optional[datetime], actor_id: Optional[int]) -> bool:
    """plan=None puts the user back on the default plan."""
    catalog = get_catalog()
    if plan is not None and plan not in catalog.plans:
        raise AccessError(f"Unknown plan '{plan}'.")
    if expires_at is not None and (plan is None or plan == catalog.default_plan):
        raise AccessError(f"The {catalog.plan_or_default(None).label} plan is the default and does not expire. Choose another plan, or leave the days blank.")
    authority.authorize(actor_id, user_id, "plan")
    return store.set_plan(user_id, plan, expires_at, actor_id)


def set_override(user_id: int, feature: str, effect: str, *, limits: Optional[dict] = None, expires_at: Optional[datetime] = None,
                 reason: str = "", actor_id: Optional[int] = None) -> None:
    definition = get_catalog().features.get(feature)
    if definition is None:
        raise AccessError(f"Unknown feature '{feature}'.")
    if effect not in (GRANT, DENY):
        raise AccessError("effect must be 'grant' or 'deny'.")
    if expires_at is not None and expires_at <= now_utc_naive():
        raise AccessError("The expiry must be in the future.")
    if limits and effect == DENY:
        raise AccessError("Limits only apply to a grant.")
    authority.authorize(actor_id, user_id, "access", feature)
    try:
        clean = parse_limits("limits", definition, limits) if limits else None
    except ValueError as e:
        raise AccessError(str(e))
    store.put_override(user_id, feature, effect, clean, expires_at, (reason or "").strip()[:500], actor_id)


def clear_override(user_id: int, feature: str, actor_id: Optional[int] = None) -> bool:
    authority.authorize(actor_id, user_id, "access", feature)
    return store.remove_override(user_id, feature, actor_id)


def overview() -> dict:
    """The configured plans and features, for the Access Control footer and the Admin Guide. Read from the catalog, so it
    always matches what is actually in force."""
    catalog = get_catalog()

    def limit_text(feature, limits):
        return [f"{feature.limits[k].label}: {v}" for k, v in limits.items() if k in feature.limits]

    plans = []
    for plan in catalog.plans.values():
        plans.append({"key": plan.key, "label": plan.label, "tone": plan.tone, "icon": plan.icon,
                      "inherits_label": catalog.plans[plan.inherits].label if plan.inherits else None,
                      "is_default": plan.key == catalog.default_plan,
                      "adds": [{"label": catalog.features[k].label, "limits": limit_text(catalog.features[k], v)} for k, v in plan.own.items()]})
    student = [{"key": f.key, "label": f.label, "description": f.description, "enforced": f.enforced,
                "plans": {p.key: (limit_text(f, p.effective[f.key]) if f.key in p.effective else None) for p in catalog.plans.values()}}
               for f in catalog.student_features()]
    admin = [{"key": f.key, "label": f.label, "description": f.description, "enforced": f.enforced, "scope": f.admin_scope, "step_up": f.step_up,
              "privileged": f.privileged, "limits": [{"key": k, "label": s.label} for k, s in f.limits.items()],
              "granted_by": catalog.features[f.granted_by].label if f.granted_by else None}
             for f in catalog.admin_features()]
    student = [{**row, "limits": [{"key": k, "label": s.label} for k, s in catalog.features[row["key"]].limits.items()]} for row in student]
    return {"default_plan": catalog.default_plan, "plans": plans, "student_features": student, "admin_features": admin}
