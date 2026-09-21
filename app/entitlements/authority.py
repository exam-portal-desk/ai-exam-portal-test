"""
app/entitlements/authority.py
Who may change whose access. The Admin role only makes someone eligible for the Admin Portal; changing plans, roles and
admin permissions is itself a privilege, and it cannot be used to climb:

  * the actor needs "Access control management" (admin.access_control);
  * nobody edits their own record, except a root administrator changing their own plan or their own user features;
  * a privileged administrator (holder of admin.access_control or admin.root) can only be changed by a root administrator,
    and a root administrator's own admin authority (role, admin features) can only be changed in the database;
  * granting or revoking an admin feature needs the feature named in its `granted_by` (Access control management for the
    ordinary ones, root for Access control management itself); `admin.root` has none: it is set in the database only.

Every write in service.py calls authorize() first, so a route cannot forget it; it never looks at what the browser showed.
"""

from typing import Dict, Optional

from app.entitlements import store
from app.entitlements.catalog import ADMIN, get_catalog
from app.entitlements.errors import AccessDenied, AccessError
from app.entitlements.resolver import GRANT, Subject, resolve
from app.utils.datetime_service import now_utc_naive

ACCESS_CONTROL = "admin.access_control"
ROOT = "admin.root"


def _holds(catalog, subject: Subject, feature: str, now) -> bool:
    """Whether the person can use the admin feature right now (admin role, an active grant, no deny)."""
    return resolve(catalog, subject, feature, now).allowed


def _granted(subject: Subject, feature: str, now) -> bool:
    """Whether the person has an active grant, whatever their role: privilege is judged by grants so that removing the
    Admin role cannot be used to hide a privileged account."""
    return any(o.feature == feature and o.effect == GRANT and o.active(now) for o in subject.overrides)


def authorize(actor_id: Optional[int], target_id: int, action: str, feature: Optional[str] = None) -> None:
    """action: "plan", "role" (grant or remove the Admin role) or "access" (a grant/deny/removal of `feature`).
    Raises AccessDenied with a reason that is safe to show. actor_id None is a trusted internal caller (billing, scripts)."""
    if actor_id is None:
        return
    catalog, now = get_catalog(), now_utc_naive()
    definition = catalog.features.get(feature) if feature else None
    if action == "access" and definition is None:
        raise AccessError(f"Unknown feature '{feature}'.")
    if action not in ("plan", "role", "access"):
        raise AccessError(f"Unknown action '{action}'.")

    actor, target = store.load_subject(actor_id), store.load_subject(target_id)
    if not _holds(catalog, actor, ACCESS_CONTROL, now):
        raise AccessDenied("You need Access control management to change plans, roles or permissions.")
    actor_root = _holds(catalog, actor, ROOT, now)
    target_root = _granted(target, ROOT, now)
    target_privileged = target_root or _granted(target, ACCESS_CONTROL, now)
    changes_authority = action == "role" or (definition is not None and definition.category == ADMIN)

    if int(actor_id) == int(target_id):
        if changes_authority or not actor_root:
            raise AccessDenied("You cannot change your own plan, role or permissions.")
    elif target_root and changes_authority:
        raise AccessDenied("A root administrator's role and admin permissions can only be changed in the database.")
    elif target_privileged and not actor_root:
        raise AccessDenied("Only a root administrator can change another privileged administrator.")

    if action == "access" and definition.category == ADMIN:
        if definition.granted_by is None:
            raise AccessDenied(f"{definition.label} cannot be granted or revoked in the application; it is set in the database.")
        if not _holds(catalog, actor, definition.granted_by, now):
            need = catalog.features[definition.granted_by].label
            raise AccessDenied(f"Granting or revoking {definition.label} needs {need}.")


def capabilities(actor_id: int, target_id: int) -> Dict[str, object]:
    """What the actor may do to this target, for the Access Control view. The server re-checks every write regardless."""
    catalog = get_catalog()
    reasons = []

    def allowed(action, feature=None) -> bool:
        try:
            authorize(actor_id, target_id, action, feature)
            return True
        except AccessDenied as e:
            reasons.append(str(e))
            return False

    student = next((f.key for f in catalog.student_features()), None)
    caps = {"plan": allowed("plan"), "role": allowed("role"),
            "special_access": bool(student) and allowed("access", student),
            "admin_features": [f.key for f in catalog.admin_features() if allowed("access", f.key)]}
    caps["reason"] = reasons[0] if reasons and not (caps["plan"] or caps["role"] or caps["special_access"] or caps["admin_features"]) else ""
    return caps
