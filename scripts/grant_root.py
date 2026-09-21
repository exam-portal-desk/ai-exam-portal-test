"""
scripts/grant_root.py
Make one existing administrator a root administrator: every admin feature, including Access control management and Root
administration. This is the only way to create a root, on purpose: the application itself can never grant Root
administration, so no admin can climb to it from inside the portal.

    python scripts/grant_root.py <username-or-email>

The account must already have the Admin role (set it in Admin > Users & Requests, or in the database). The feature list is read
from config/entitlements.json. Safe to run twice; every grant is recorded in access_audit_log.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2                                        # noqa: E402
import psycopg2.extras                                 # noqa: E402  (Json adapter)

import app.config as config                            # noqa: E402
from app.entitlements.catalog import get_catalog       # noqa: E402


def main(who: str) -> int:
    keys = [f.key for f in get_catalog().admin_features()]
    with psycopg2.connect(config.DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, username, role FROM users WHERE username = %s OR email = %s", (who, who))
        row = cur.fetchone()
        if not row:
            print(f"No user '{who}'.")
            return 1
        uid, username, role = row
        if "admin" not in str(role or "").lower():
            print(f"{username} does not have the Admin role (role: {role}). Give them the Admin role first.")
            return 1
        for key in keys:
            cur.execute(
                "INSERT INTO user_feature_overrides (user_id, feature_key, effect, reason) VALUES (%s, %s, 'grant', %s) "
                "ON CONFLICT (user_id, feature_key) DO UPDATE SET effect = 'grant', expires_at = NULL", (uid, key, "Root administrator (scripts/grant_root.py)"))
        cur.execute("INSERT INTO access_audit_log (actor_id, target_user_id, action, detail) VALUES (NULL, %s, 'root_granted', %s)",
                    (uid, psycopg2.extras.Json({"features": keys, "via": "scripts/grant_root.py"})))
    print(f"{username} is now a root administrator ({len(keys)} admin features). They may need to sign in again.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1].strip()))
