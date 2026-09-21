"""
app/entitlements/catalog.py
The feature/plan catalog, read from config/entitlements.json. It is the only place that knows the file format:
everything else receives an immutable, validated Catalog. A changed file is swapped in only if it validates; a broken
edit keeps serving the last good catalog.
"""

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Tuple

import app.config as config

STUDENT = "student"
ADMIN = "admin"
ADMIN_PREFIX = "admin."
SCOPE_ALL = "all"
SCOPE_GRANTED = "granted"
GRANTED_BY_NOBODY = "none"       # a privilege that cannot be handed out inside the application
DEFAULT_GRANTOR = "admin.access_control"
# How a plan chip looks (static/shared/plan-badge.css); the icon is a Font Awesome name such as "fa-crown".
TONES = ("dark", "royal", "crimson", "neutral", "info", "accent", "success", "warning")
_ICON = re.compile(r"^fa-[a-z0-9-]+$")
STEP_UP_METHODS = ("passkey",)

_RELOAD_CHECK_SECONDS = 30


class EntitlementConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LimitSpec:
    key: str
    label: str
    per: str = ""


@dataclass(frozen=True)
class Feature:
    key: str
    category: str
    label: str
    description: str
    enforced: bool
    admin_scope: str
    limits: Mapping[str, LimitSpec]
    step_up: str = ""   # extra authentication a sensitive feature will require; not enforced yet
    privileged: bool = False                 # holding it makes someone a privileged administrator (only root may change them)
    granted_by: Optional[str] = DEFAULT_GRANTOR   # the admin feature a person needs to grant or revoke this one; None = only in the database


@dataclass(frozen=True)
class Plan:
    key: str
    label: str
    tone: str
    icon: str
    inherits: Optional[str]
    own: Mapping[str, Mapping[str, int]]        # what this plan itself declares
    effective: Mapping[str, Mapping[str, int]]  # feature -> limits, after inheritance (empty limits = unmetered)


@dataclass(frozen=True)
class Catalog:
    default_plan: str
    features: Mapping[str, Feature]
    plans: Mapping[str, Plan]
    mtime: float = field(default=0.0, compare=False)

    def plan_or_default(self, key: Optional[str]) -> Plan:
        return self.plans.get(key or "") or self.plans[self.default_plan]

    def entry_plan(self, feature_key: str) -> Optional[Plan]:
        """The first plan (in file order) that includes the feature: what a user is told to move to for it."""
        return next((p for p in self.plans.values() if feature_key in p.effective), None)

    def entry_terms(self, feature_key: str) -> Mapping[str, int]:
        """Limits of the entry plan. A feature that is not enforced yet is open to everyone, on these terms for users whose
        own plan does not include it."""
        plan = self.entry_plan(feature_key)
        return plan.effective[feature_key] if plan else {}

    def student_features(self) -> Tuple[Feature, ...]:
        return tuple(f for f in self.features.values() if f.category == STUDENT)

    def admin_features(self) -> Tuple[Feature, ...]:
        return tuple(f for f in self.features.values() if f.category == ADMIN)


def _fail(message: str):
    raise EntitlementConfigError(message)


def _text(value, where: str, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        _fail(f"{where} must be a non-empty string.")
    return value.strip()


def _parse_feature(key: str, raw) -> Feature:
    where = f"features.{key}"
    if not isinstance(raw, dict):
        _fail(f"{where} must be an object.")
    category = raw.get("category")
    if category not in (STUDENT, ADMIN):
        _fail(f"{where}.category must be '{STUDENT}' or '{ADMIN}'.")
    if (category == ADMIN) != key.startswith(ADMIN_PREFIX):
        _fail(f"{where}: admin features (and only those) must be named '{ADMIN_PREFIX}<name>'.")
    scope = raw.get("admin_scope", SCOPE_GRANTED)
    if category == ADMIN and scope not in (SCOPE_ALL, SCOPE_GRANTED):
        _fail(f"{where}.admin_scope must be '{SCOPE_ALL}' or '{SCOPE_GRANTED}'.")
    if not isinstance(raw.get("enforced", False), bool):
        _fail(f"{where}.enforced must be true or false.")
    step_up = raw.get("step_up", "")
    if step_up and step_up not in STEP_UP_METHODS:
        _fail(f"{where}.step_up must be one of: {', '.join(STEP_UP_METHODS)}.")
    if not isinstance(raw.get("privileged", False), bool):
        _fail(f"{where}.privileged must be true or false.")
    grantor = raw.get("granted_by", DEFAULT_GRANTOR)
    limits = {}
    for lkey, spec in (raw.get("limits") or {}).items():
        if not isinstance(spec, dict):
            _fail(f"{where}.limits.{lkey} must be an object with a label.")
        limits[lkey] = LimitSpec(lkey, _text(spec.get("label"), f"{where}.limits.{lkey}.label"), _text(spec.get("per"), "per", False))
    return Feature(key, category, _text(raw.get("label"), f"{where}.label"), _text(raw.get("description"), f"{where}.description", False),
                   bool(raw.get("enforced", False)), scope if category == ADMIN else "", limits, step_up,
                   bool(raw.get("privileged", False)), None if grantor == GRANTED_BY_NOBODY else grantor)


def parse_limits(where: str, feature: Feature, raw) -> Dict[str, int]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _fail(f"{where} must be an object.")
    out = {}
    for lkey, value in raw.items():
        if lkey not in feature.limits:
            _fail(f"{where}.{lkey}: '{feature.key}' does not declare that limit.")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail(f"{where}.{lkey} must be a whole number of 0 or more (leave the limit out for no limit).")
        out[lkey] = value
    return out


def _parse_own(plan_key: str, raw: dict, features: Mapping[str, Feature]) -> Dict[str, Optional[Dict[str, int]]]:
    """A plan's own entries: feature -> limits, or None when the plan removes a feature it would inherit."""
    own = {}
    for fkey, entry in (raw.get("features") or {}).items():
        where = f"plans.{plan_key}.features.{fkey}"
        feature = features.get(fkey)
        if feature is None:
            _fail(f"{where}: unknown feature.")
        if feature.category != STUDENT:
            _fail(f"{where}: admin permissions are never part of a plan.")
        if not isinstance(entry, dict):
            _fail(f"{where} must be an object ({{}} for a feature with no limits).")
        own[fkey] = parse_limits(f"{where}.limits", feature, entry.get("limits")) if entry.get("enabled", True) else None
    return own


def build(data) -> Catalog:
    if not isinstance(data, dict):
        _fail("The file must contain a JSON object.")
    features = {key: _parse_feature(key, raw) for key, raw in (data.get("features") or {}).items()}
    for feature in features.values():
        if feature.category == ADMIN and feature.granted_by is not None:
            grantor = features.get(feature.granted_by)
            if grantor is None or grantor.category != ADMIN:
                _fail(f"features.{feature.key}.granted_by must name an admin feature or '{GRANTED_BY_NOBODY}'.")
    raw_plans = data.get("plans") or {}
    if not raw_plans:
        _fail("At least one plan is required.")
    default_plan = data.get("default_plan")
    if default_plan not in raw_plans:
        _fail("default_plan must name a plan defined under 'plans'.")

    plans: Dict[str, Plan] = {}

    def resolve(key: str, trail: Tuple[str, ...]) -> Plan:
        if key in plans:
            return plans[key]
        if key in trail:
            _fail(f"Plans inherit each other in a circle: {' -> '.join(trail + (key,))}.")
        raw = raw_plans[key]
        parent_key = raw.get("inherits")
        if parent_key is not None and parent_key not in raw_plans:
            _fail(f"plans.{key}.inherits: unknown plan '{parent_key}'.")
        effective = dict(resolve(parent_key, trail + (key,)).effective) if parent_key else {}
        own = _parse_own(key, raw, features)
        for fkey, limits in own.items():
            if limits is None:
                effective.pop(fkey, None)
            else:
                effective[fkey] = limits
        tone = raw.get("tone", "neutral")
        if tone not in TONES:
            _fail(f"plans.{key}.tone must be one of: {', '.join(TONES)}.")
        icon = raw.get("icon", "fa-star")
        if not isinstance(icon, str) or not _ICON.match(icon):
            _fail(f"plans.{key}.icon must be a Font Awesome name such as 'fa-crown'.")
        plans[key] = Plan(key, _text(raw.get("label"), f"plans.{key}.label"), tone, icon, parent_key,
                          {k: v for k, v in own.items() if v is not None}, effective)
        return plans[key]

    for key in raw_plans:
        resolve(key, ())
    return Catalog(default_plan, features, plans)


_lock = threading.Lock()
_catalog: Optional[Catalog] = None
_checked_at = 0.0


def _read(path: str) -> Catalog:
    mtime = os.stat(path).st_mtime
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            _fail(f"entitlements.json is not valid JSON: {e}")
    built = build(data)
    return Catalog(built.default_plan, built.features, built.plans, mtime)


def get_catalog() -> Catalog:
    global _catalog, _checked_at
    now = time.monotonic()
    if _catalog is not None and now - _checked_at < _RELOAD_CHECK_SECONDS:
        return _catalog
    with _lock:
        now = time.monotonic()
        if _catalog is not None and now - _checked_at < _RELOAD_CHECK_SECONDS:
            return _catalog
        path = config.ENTITLEMENTS_CONFIG_PATH
        try:
            if _catalog is None or os.stat(path).st_mtime != _catalog.mtime:
                _catalog = _read(path)
        except Exception as e:
            if _catalog is None:
                raise
            print(f"[entitlements] keeping previous catalog; reload failed: {e}")
        _checked_at = now
        return _catalog
