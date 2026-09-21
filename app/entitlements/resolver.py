"""
app/entitlements/resolver.py
The access decision, as pure functions: a Catalog and a Subject in, an Access out. No I/O, so every rule is
testable and a check costs nothing beyond a few dict lookups.

Precedence, for a feature the catalog enforces:
  1. an active DENY override for the user always wins, over every other layer (including the admin role);
  2. admin features (admin.*): the user must hold the admin role AND have an active GRANT override for that feature (the role
     alone gives nothing; admin_scope "all" is the one exception a feature may opt into). A grant may carry limits.
     Plans never grant admin features;
  3. student features: the feature must be in the user's effective plan, or the user needs an active GRANT override.
     The admin role never grants a student feature;
  4. overrides and paid plans stop applying at their expiry.
A feature the catalog does not enforce yet is allowed for everyone (source "ungated"), so it keeps today's behaviour. Its
limits still come from the configuration: the user's own plan when it includes the feature (plus limits on a grant), otherwise
the entry terms of the first plan that does. Denies do not apply until the feature is enforced.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Optional, Tuple

from app.entitlements.catalog import ADMIN, SCOPE_ALL, Catalog, Plan

GRANT = "grant"
DENY = "deny"


@dataclass(frozen=True)
class Override:
    feature: str
    effect: str
    limits: Mapping[str, int] = field(default_factory=dict)
    expires_at: Optional[datetime] = None
    reason: str = ""
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None

    def active(self, now: datetime) -> bool:
        return self.expires_at is None or self.expires_at > now


@dataclass(frozen=True)
class Subject:
    user_id: int
    is_admin: bool = False
    plan: Optional[str] = None
    plan_expires_at: Optional[datetime] = None
    overrides: Tuple[Override, ...] = ()


@dataclass(frozen=True)
class Access:
    feature: str
    allowed: bool
    source: str
    plan: str = ""
    enforced: bool = True
    limits: Mapping[str, int] = field(default_factory=dict)
    expires_at: Optional[datetime] = None
    step_up: str = ""


@dataclass(frozen=True)
class LimitStatus:
    limit: Optional[int]
    used: int
    remaining: Optional[int]
    allowed: bool


def effective_plan(catalog: Catalog, subject: Subject, now: datetime) -> Plan:
    if subject.plan and (subject.plan_expires_at is None or subject.plan_expires_at > now):
        return catalog.plan_or_default(subject.plan)
    return catalog.plan_or_default(None)


def resolve(catalog: Catalog, subject: Optional[Subject], feature_key: str, now: datetime, force: bool = False) -> Access:
    """`force` reports what the plan and overrides say even for a feature that is not enforced yet (for Admin views)."""
    feature = catalog.features.get(feature_key)
    if feature is None:
        return Access(feature_key, False, "unknown_feature")
    gated = feature.enforced or force
    if subject is None:
        if gated:
            return Access(feature_key, False, "no_subject", step_up=feature.step_up)
        return Access(feature_key, True, "ungated", enforced=False, limits=dict(catalog.entry_terms(feature_key)))

    plan = effective_plan(catalog, subject, now)
    active = [o for o in subject.overrides if o.feature == feature_key and o.active(now)]
    if not gated:
        grant = next((o for o in active if o.effect == GRANT), None)
        terms = plan.effective.get(feature_key)
        limits = {**(catalog.entry_terms(feature_key) if terms is None else terms), **(grant.limits if grant else {})}
        return Access(feature_key, True, "ungated", plan.key, False, limits, grant.expires_at if grant else None)

    def decision(allowed: bool, source: str, limits=None, expires_at=None) -> Access:
        return Access(feature_key, allowed, source, plan.key, True, limits or {}, expires_at, feature.step_up)

    deny = next((o for o in active if o.effect == DENY), None)
    if deny:
        return decision(False, "override_deny", expires_at=deny.expires_at)
    grant = next((o for o in active if o.effect == GRANT), None)

    if feature.category == ADMIN:
        if not subject.is_admin:
            return decision(False, "not_admin")
        if grant:
            return decision(True, "override_grant", dict(grant.limits), grant.expires_at)
        if feature.admin_scope == SCOPE_ALL:
            return decision(True, "admin_role")
        return decision(False, "admin_permission_required")

    plan_limits = plan.effective.get(feature_key)
    if grant:
        return decision(True, "override_grant", {**(plan_limits or {}), **grant.limits}, grant.expires_at)
    if plan_limits is not None:
        return decision(True, "plan", dict(plan_limits))
    return decision(False, "not_in_plan")


def check_limit(access: Access, limit_key: str, used: int, amount: int = 1) -> LimitStatus:
    """A limit that is not configured is unlimited. `used` is what the caller already knows (a counter it keeps, or a
    resource count it just read), so checking never costs a query of its own."""
    limit = access.limits.get(limit_key)
    if limit is None:
        return LimitStatus(None, used, None, access.allowed)
    remaining = max(0, limit - used)
    return LimitStatus(limit, used, remaining, access.allowed and used + amount <= limit)
