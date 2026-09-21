"""
app/entitlements/errors.py
"""


class AccessError(ValueError):
    """A request to change access that the catalog does not allow (unknown plan/feature, bad limit)."""


class AccessDenied(AccessError):
    """The person making the change is not allowed to make it (see app/entitlements/authority.py)."""
